from __future__ import annotations

import pytest

from fermilink.curated_channels import normalize_channel_id, resolve_curated_package


def test_curated_alias_resolution() -> None:
    assert normalize_channel_id("tle-research-group") == "tel-research-group"


def test_resolve_curated_package() -> None:
    pkg = resolve_curated_package("ase", channel="tel-research-group")
    assert "TEL-Research-Group/ase" in pkg.zip_url


def test_resolve_curated_package_missing() -> None:
    with pytest.raises(ValueError):
        resolve_curated_package("not-a-real-package", channel="tel-research-group")
