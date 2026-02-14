from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import curated_channels
from fermilink.curated_channels import (
    list_curated_packages,
    normalize_channel_id,
    resolve_curated_package,
    select_package_version,
)


def test_curated_alias_resolution() -> None:
    assert normalize_channel_id("tle-research-group") == "tel-research-group"


def test_resolve_curated_package() -> None:
    pkg = resolve_curated_package("ase", channel="tel-research-group")
    assert "TEL-Research-Group/ase" in pkg.zip_url
    assert pkg.default_version == "branch-head"
    assert pkg.description
    assert pkg.upstream_repo_url
    selected = select_package_version(pkg)
    assert selected.version_id == "branch-head"
    assert selected.source_archive_url == pkg.zip_url


def test_resolve_curated_package_missing() -> None:
    with pytest.raises(ValueError):
        resolve_curated_package("not-a-real-package", channel="tel-research-group")


def test_list_curated_packages_contains_tel_entries() -> None:
    packages = list_curated_packages(channel="tel-research-group")
    assert "ase" in packages
    assert "maxwelllink" in packages


def test_select_curated_package_version_missing_raises() -> None:
    pkg = resolve_curated_package("ase", channel="tel-research-group")
    with pytest.raises(ValueError):
        select_package_version(pkg, version_id="v0.0.1")


def test_curated_loader_supports_v1_payload(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "curated_channels"
    data_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "packages": [
            {
                "package_id": "demo",
                "title": "Demo",
                "zip_url": "https://example.invalid/demo.zip",
            }
        ]
    }
    (data_dir / "tel-research-group.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )

    curated_channels._available_channel_ids.cache_clear()
    curated_channels._load_channel_packages.cache_clear()
    monkeypatch.setattr(curated_channels, "DATA_DIR", data_dir)

    packages = curated_channels.list_curated_packages(channel="tel-research-group")
    assert "demo" in packages
    demo = packages["demo"]
    assert demo.default_version == "branch-head"
    assert demo.zip_url == "https://example.invalid/demo.zip"
    assert len(demo.versions) == 1
    assert demo.versions[0].version_id == "branch-head"

    curated_channels._available_channel_ids.cache_clear()
    curated_channels._load_channel_packages.cache_clear()
