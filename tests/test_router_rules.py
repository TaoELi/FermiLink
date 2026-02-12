from __future__ import annotations

from pathlib import Path

from fermilink.package_registry import install_from_local_path
from fermilink.router_rules import sync_router_rules


def _make_local_package(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "skills").mkdir()
    (path / "skills" / "README.md").write_text("skills", encoding="utf-8")


def test_sync_router_rules_creates_file(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    ase_source = tmp_path / "ase-src"
    _make_local_package(ase_source)

    install_from_local_path(scipkg_root, "ase", local_path=ase_source, activate=True)

    result = sync_router_rules(scipkg_root)
    payload = result["payload"]

    assert payload["default_package_id"] == "ase"
    assert "ase" in payload["packages"]
    assert (scipkg_root / "router_rules.json").exists()
