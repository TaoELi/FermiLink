from __future__ import annotations

from pathlib import Path

import pytest

from fermilink.packages.package_registry import (
    PackageNotFoundError,
    install_from_local_path,
    load_registry,
    normalize_package_id,
    set_package_dependency_ids,
)


def _make_local_package(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "skills").mkdir()
    (path / "docs").mkdir()
    (path / "skills" / "README.md").write_text("skills", encoding="utf-8")


def test_normalize_package_id() -> None:
    assert normalize_package_id(" ASE Package ") == "ase-package"


def test_install_local_and_activate(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    source = tmp_path / "ase-src"
    _make_local_package(source)

    meta = install_from_local_path(
        scipkg_root,
        "ase",
        local_path=source,
        activate=True,
        force=False,
    )

    registry = load_registry(scipkg_root)
    assert meta["id"] == "ase"
    assert registry["active_package"] == "ase"
    assert "ase" in registry["packages"]


def test_dependencies_require_installed_packages(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    source_a = tmp_path / "pkg-a"
    source_b = tmp_path / "pkg-b"
    _make_local_package(source_a)
    _make_local_package(source_b)

    install_from_local_path(scipkg_root, "maxwelllink", local_path=source_a)
    install_from_local_path(scipkg_root, "meep", local_path=source_b)

    meta = set_package_dependency_ids(
        scipkg_root,
        "maxwelllink",
        ["meep"],
    )
    assert meta["dependency_package_ids"] == ["meep"]

    with pytest.raises(PackageNotFoundError):
        set_package_dependency_ids(scipkg_root, "maxwelllink", ["not-installed"])
