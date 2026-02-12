from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("chainlit")

_TEMP_ROOT = tempfile.mkdtemp(prefix="fermilink-web-tests-")
os.environ.setdefault("CHAINLIT_APP_ROOT", _TEMP_ROOT)
os.environ.setdefault("SCIPKG_ROOT", str(Path(_TEMP_ROOT) / "scientific_packages"))
os.environ.setdefault("CHAINLIT_AUTH_SECRET", "test-secret")

from fermilink.web import app as web_app


def test_branding_defaults_are_applied_when_config_missing_custom_assets() -> None:
    assert web_app.config.ui.custom_css == "/public/custom.css"
    assert web_app.config.ui.custom_js == "/public/custom.js"
    assert web_app.config.ui.logo_file_url == "/public/fermilink_wordmark.svg"
    assert web_app.config.ui.default_avatar_file_url == "/public/fermilink_mini_bright.svg"


def test_packaged_fermilink_markdown_is_synced_to_chainlit_md() -> None:
    source = web_app.PACKAGED_CHAINLIT_MARKDOWN
    target = web_app.TARGET_CHAINLIT_MARKDOWN
    assert source.is_file()
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_normalize_rule_terms_deduplicates_and_normalizes() -> None:
    raw = [" ASE ", "ase", "Meep", "", "meep"]
    assert web_app._normalize_rule_terms(raw) == ["ase", "meep"]


def test_load_router_config_normalizes_payload(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    scipkg_root.mkdir(parents=True, exist_ok=True)
    payload = {
        "default_package_id": "ASE",
        "min_score": 4,
        "min_margin": 2,
        "packages": {
            "ASE": {"keywords": ["atoms object"]},
            "Meep": {"strong_keywords": ["fdtd"]},
            "invalid": "not-a-dict",
        },
    }
    (scipkg_root / web_app.PACKAGE_ROUTER_RULES_FILENAME).write_text(
        json.dumps(payload), encoding="utf-8"
    )

    config = web_app._load_router_config(scipkg_root)
    assert config["default_package_id"] == "ase"
    assert config["min_score"] == 4
    assert config["min_margin"] == 2
    assert sorted(config["packages"].keys()) == ["ase", "meep"]


def test_route_package_candidate_selects_best_match() -> None:
    decision = web_app._route_package_candidate(
        user_text="I need a meep fdtd waveguide with pml setup",
        package_ids=["maxwelllink", "meep"],
        current_package_id=None,
        config={"min_score": 1, "min_margin": 1, "packages": {}},
    )
    assert decision["selected_package_id"] == "meep"
    assert decision["reason"] == "matched"


def test_parse_package_command_variants() -> None:
    assert web_app._parse_package_command("/package list") == ("list", [])
    assert web_app._parse_package_command("/package use ase") == ("use", ["ase"])
    assert web_app._parse_package_command("/package ase") == ("use", ["ase"])
    assert web_app._parse_package_command("hello") is None


def test_should_surface_runner_log_respects_flag_and_filters(monkeypatch) -> None:
    monkeypatch.setattr(web_app, "FORWARD_RUNNER_LOGS", False)
    assert web_app._should_surface_runner_log("some stderr message") is False

    monkeypatch.setattr(web_app, "FORWARD_RUNNER_LOGS", True)
    suppressed = (
        "2026-01-01T00:00:00Z ERROR codex_core::rollout::list: "
        "state db missing rollout path for thread abc"
    )
    assert web_app._should_surface_runner_log(suppressed) is False
    assert web_app._should_surface_runner_log("tool failed with exit code 1") is True
