from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fermilink.packages import package_registry
from fermilink.packages.package_registry import (
    PackageNotFoundError,
    PACKAGE_WORKFLOW_TYPE_KEY,
    install_from_git_url,
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
    assert meta[PACKAGE_WORKFLOW_TYPE_KEY] == "simulation"
    assert registry["active_package"] == "ase"
    assert "ase" in registry["packages"]


def test_install_local_records_experiment_workflow_type(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    source = tmp_path / "exp-src"
    _make_local_package(source)

    meta = install_from_local_path(
        scipkg_root,
        "exp",
        local_path=source,
        workflow_type="experiment",
    )

    registry = load_registry(scipkg_root)
    assert meta[PACKAGE_WORKFLOW_TYPE_KEY] == "experiment"
    assert registry["packages"]["exp"][PACKAGE_WORKFLOW_TYPE_KEY] == "experiment"


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


def test_install_from_local_path_force_allows_source_equal_target(
    tmp_path: Path,
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    source = scipkg_root / "packages" / "ase"
    _make_local_package(source)

    meta = install_from_local_path(
        scipkg_root,
        "ase",
        local_path=source,
        activate=False,
        force=True,
    )

    registry = load_registry(scipkg_root)
    assert meta["id"] == "ase"
    assert (source / "skills" / "README.md").is_file()
    assert registry["packages"]["ase"]["installed_path"] == str(source.resolve())


def test_install_from_git_url_clones_with_ssh_and_registers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output is True
        assert text is True
        assert check is False
        calls.append(command)
        target_dir = Path(command[-1])
        _make_local_package(target_dir)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(package_registry.subprocess, "run", fake_run)

    meta = install_from_git_url(
        scipkg_root,
        "mos2-quantum-transport-skill",
        git_url="https://github.com/TaoELi/mos2-quantum-transport-skill",
        activate=True,
        workflow_type="experiment",
    )

    registry = load_registry(scipkg_root)
    assert calls == [
        [
            "git",
            "clone",
            "git@github.com:TaoELi/mos2-quantum-transport-skill.git",
            str(scipkg_root / "packages" / "mos2-quantum-transport-skill"),
        ]
    ]
    assert meta["id"] == "mos2-quantum-transport-skill"
    assert (
        meta["source"] == "git:https://github.com/TaoELi/mos2-quantum-transport-skill"
    )
    assert meta[PACKAGE_WORKFLOW_TYPE_KEY] == "experiment"
    assert registry["active_package"] == "mos2-quantum-transport-skill"
