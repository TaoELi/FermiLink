from __future__ import annotations

from pathlib import Path

from fermilink import cli
from fermilink.package_registry import load_registry


def _make_local_package(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "skills").mkdir()
    (path / "skills" / "README.md").write_text("skills", encoding="utf-8")


def test_cli_install_local_auto_sync(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "ase-src"
    _make_local_package(source)

    code = cli.main(["install", "ase", "--local-path", str(source), "--activate"])
    assert code == 0

    registry = load_registry(scipkg_root)
    assert registry["active_package"] == "ase"
    assert (scipkg_root / "router_rules.json").exists()


def test_cli_dependencies(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    source_a = tmp_path / "maxwell-src"
    source_b = tmp_path / "meep-src"
    _make_local_package(source_a)
    _make_local_package(source_b)

    assert cli.main(["install", "maxwelllink", "--local-path", str(source_a)]) == 0
    assert cli.main(["install", "meep", "--local-path", str(source_b)]) == 0

    code = cli.main(["dependencies", "maxwelllink", "--package", "meep"])
    assert code == 0

    registry = load_registry(scipkg_root)
    deps = registry["packages"]["maxwelllink"].get("dependency_package_ids")
    assert deps == ["meep"]
