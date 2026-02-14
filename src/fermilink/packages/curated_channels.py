from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ChannelPackage:
    package_id: str
    title: str
    zip_url: str
    description: str | None = None
    upstream_repo_url: str | None = None
    homepage_url: str | None = None
    tags: tuple[str, ...] = ()
    default_version: str = "branch-head"
    versions: tuple["ChannelPackageVersion", ...] = ()


@dataclass(frozen=True)
class ChannelPackageVersion:
    version_id: str
    source_archive_url: str
    source_ref_type: str | None = None
    source_ref_value: str | None = None
    verified: bool = False
    notes: str | None = None


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "curated_channels"

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


def _normalize_tag_list(raw: Any) -> tuple[str, ...]:
    if isinstance(raw, str):
        items = [raw]
    elif isinstance(raw, list):
        items = [str(item) for item in raw if isinstance(item, str)]
    else:
        items = []
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = item.strip()
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return tuple(deduped)


def _parse_versions(item: dict[str, Any], zip_url: str, package_id: str) -> tuple[ChannelPackageVersion, ...]:
    versions_raw = item.get("versions")
    parsed_versions: list[ChannelPackageVersion] = []

    if isinstance(versions_raw, list):
        for version_index, raw_version in enumerate(versions_raw, start=1):
            if not isinstance(raw_version, dict):
                raise ValueError(
                    f"Package '{package_id}' has invalid version at index {version_index} (not an object)"
                )
            version_id = str(raw_version.get("version_id") or "").strip()
            source_archive_url = str(raw_version.get("source_archive_url") or "").strip()
            if not version_id or not source_archive_url:
                raise ValueError(
                    f"Package '{package_id}' version at index {version_index} missing version_id/source_archive_url"
                )
            source_ref_raw = raw_version.get("source_ref")
            source_ref_type: str | None = None
            source_ref_value: str | None = None
            if isinstance(source_ref_raw, dict):
                ref_type = str(source_ref_raw.get("type") or "").strip()
                ref_value = str(source_ref_raw.get("value") or "").strip()
                source_ref_type = ref_type or None
                source_ref_value = ref_value or None
            verified_raw = raw_version.get("verified")
            verified = bool(verified_raw) if isinstance(verified_raw, bool) else False
            notes_raw = raw_version.get("notes")
            notes = notes_raw.strip() if isinstance(notes_raw, str) and notes_raw.strip() else None
            parsed_versions.append(
                ChannelPackageVersion(
                    version_id=version_id,
                    source_archive_url=source_archive_url,
                    source_ref_type=source_ref_type,
                    source_ref_value=source_ref_value,
                    verified=verified,
                    notes=notes,
                )
            )

    if not parsed_versions:
        if not zip_url:
            raise ValueError(f"Package '{package_id}' must provide either versions[] or zip_url")
        parsed_versions.append(
            ChannelPackageVersion(
                version_id="branch-head",
                source_archive_url=zip_url,
                source_ref_type=None,
                source_ref_value=None,
                verified=False,
            )
        )
    return tuple(parsed_versions)


def select_package_version(
    package: ChannelPackage,
    *,
    version_id: str | None = None,
) -> ChannelPackageVersion:
    versions = package.versions
    if not versions:
        return ChannelPackageVersion(
            version_id="branch-head",
            source_archive_url=package.zip_url,
            verified=False,
        )

    selected_id = (version_id or package.default_version or "").strip()
    if not selected_id:
        return versions[0]

    selected_key = selected_id.lower()
    for version in versions:
        if version.version_id.strip().lower() == selected_key:
            return version

    available = ", ".join(version.version_id for version in versions)
    raise ValueError(
        f"Version '{selected_id}' not found for package '{package.package_id}'. Available versions: {available}"
    )


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

    schema_version = payload.get("schema_version")
    if schema_version is not None and not isinstance(schema_version, int):
        raise ValueError(f"Invalid schema_version in curated channel file: {channel_path}")

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
        description = item.get("description")
        description_text = description.strip() if isinstance(description, str) and description.strip() else None
        upstream_repo_url = item.get("upstream_repo_url")
        upstream_repo_url_text = (
            upstream_repo_url.strip()
            if isinstance(upstream_repo_url, str) and upstream_repo_url.strip()
            else None
        )
        homepage_url = item.get("homepage_url")
        homepage_url_text = homepage_url.strip() if isinstance(homepage_url, str) and homepage_url.strip() else None
        tags = _normalize_tag_list(item.get("tags"))
        versions = _parse_versions(item, zip_url, package_id)
        default_version = str(item.get("default_version") or "").strip()
        if not default_version:
            default_version = versions[0].version_id
        version_ids = {version.version_id for version in versions}
        if default_version not in version_ids:
            raise ValueError(
                f"Package '{package_id}' default_version '{default_version}' not in versions in {channel_path}"
            )
        resolved_zip_url = zip_url
        if not resolved_zip_url:
            for version in versions:
                if version.version_id == default_version:
                    resolved_zip_url = version.source_archive_url
                    break
        if not package_id or not title or not resolved_zip_url:
            raise ValueError(
                f"Curated package at index {index} missing package_id/title/zip_url in {channel_path}"
            )
        packages[package_id] = ChannelPackage(
            package_id=package_id,
            title=title,
            zip_url=resolved_zip_url,
            description=description_text,
            upstream_repo_url=upstream_repo_url_text,
            homepage_url=homepage_url_text,
            tags=tags,
            default_version=default_version,
            versions=versions,
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
