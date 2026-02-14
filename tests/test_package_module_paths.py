from __future__ import annotations

import importlib


def test_package_registry_legacy_path_aliases_new_module() -> None:
    legacy = importlib.import_module("fermilink.package_registry")
    modern = importlib.import_module("fermilink.packages.package_registry")
    assert legacy is modern


def test_curated_channels_legacy_path_aliases_new_module() -> None:
    legacy = importlib.import_module("fermilink.curated_channels")
    modern = importlib.import_module("fermilink.packages.curated_channels")
    assert legacy is modern


def test_package_core_legacy_path_aliases_new_module() -> None:
    legacy = importlib.import_module("fermilink._package_core")
    modern = importlib.import_module("fermilink.packages._package_core")
    assert legacy is modern
