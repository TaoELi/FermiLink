from __future__ import annotations

from pathlib import Path

from fermilink.config import (
    resolve_fermilink_home,
    resolve_runtime_root,
    resolve_scipkg_root,
    resolve_workspaces_root,
)
from fermilink.runner.scientific_packages import (
    resolve_scipkg_root as runner_resolve_scipkg_root,
)


def test_default_roots_use_home_fermilink(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("FERMILINK_HOME", raising=False)
    monkeypatch.delenv("FERMILINK_SCIPKG_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_SCIENTIFIC_PACKAGES_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_RUNTIME_ROOT", raising=False)

    expected_home = home / ".fermilink"
    assert resolve_fermilink_home() == expected_home
    assert resolve_scipkg_root() == expected_home / "scientific_packages"
    assert runner_resolve_scipkg_root() == expected_home / "scientific_packages"
    assert resolve_workspaces_root() == expected_home / "workspaces"
    assert resolve_runtime_root() == expected_home / "runtime"


def test_fermilink_home_override_drives_default_roots(
    monkeypatch, tmp_path: Path
) -> None:
    custom_home = (tmp_path / "custom-root").resolve()
    monkeypatch.setenv("FERMILINK_HOME", str(custom_home))
    monkeypatch.delenv("FERMILINK_SCIPKG_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_SCIENTIFIC_PACKAGES_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_WORKSPACES_ROOT", raising=False)
    monkeypatch.delenv("FERMILINK_RUNTIME_ROOT", raising=False)

    assert resolve_fermilink_home() == custom_home
    assert resolve_scipkg_root() == custom_home / "scientific_packages"
    assert resolve_workspaces_root() == custom_home / "workspaces"
    assert resolve_runtime_root() == custom_home / "runtime"
