from __future__ import annotations

import json
from pathlib import Path

from fermilink import router_rules
from fermilink.packages.package_registry import install_from_local_path
from fermilink.router_rules import infer_rule, load_family_hints, sync_router_rules


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


def test_router_family_hints_loaded_from_json() -> None:
    hints = load_family_hints()
    assert "maxwelllink" in hints
    inferred = infer_rule("maxwelllink")
    assert "quantum optics" in inferred["strong_keywords"]


def test_router_family_hints_supports_package_id_overrides(
    monkeypatch, tmp_path: Path
) -> None:
    hints_path = tmp_path / "family_hints.json"
    payload = {
        "schema_version": 2,
        "families": {
            "kwant": {
                "description": "Routing hints for kwant.",
                "strong_keywords": ["quantum transport"],
                "keywords": ["mesoscopic"],
                "negative_keywords": [],
                "package_id_overrides": ["quantum-transport-kit"],
            }
        },
    }
    hints_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    router_rules.load_family_hints.cache_clear()
    monkeypatch.setattr(router_rules, "FAMILY_HINTS_PATH", hints_path)

    inferred = router_rules.infer_rule("quantum-transport-kit")
    assert "quantum transport" in inferred["strong_keywords"]
    assert "mesoscopic" in inferred["keywords"]

    router_rules.load_family_hints.cache_clear()
