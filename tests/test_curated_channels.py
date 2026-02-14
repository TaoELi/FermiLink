from __future__ import annotations

import pytest

from fermilink.curated_channels import (
    list_curated_packages,
    normalize_channel_id,
    resolve_curated_package,
)


def test_curated_alias_resolution() -> None:
    assert normalize_channel_id("tle-research-group") == "tel-research-group"


def test_resolve_curated_package() -> None:
    pkg = resolve_curated_package("ase", channel="tel-research-group")
    assert "TEL-Research-Group/ase" in pkg.zip_url


def test_resolve_curated_package_missing() -> None:
    with pytest.raises(ValueError):
        resolve_curated_package("not-a-real-package", channel="tel-research-group")


def test_list_curated_packages_contains_tel_entries() -> None:
    packages = list_curated_packages(channel="tel-research-group")
    assert "ase" in packages
    assert "maxwelllink" in packages
