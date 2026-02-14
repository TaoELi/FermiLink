from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _script_path() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "validate_data.py"


def test_validate_data_script_passes_for_valid_payload(tmp_path: Path) -> None:
    curated_payload = {
        "schema_version": 2,
        "channel_id": "tel-research-group",
        "packages": [
            {
                "package_id": "demo",
                "title": "Demo",
                "description": "Demo package",
                "upstream_repo_url": "https://example.invalid/demo",
                "homepage_url": "https://example.invalid/demo/home",
                "default_version": "branch-head",
                "versions": [
                    {
                        "version_id": "branch-head",
                        "source_archive_url": "https://example.invalid/demo/archive/main.zip",
                        "source_ref": {"type": "branch", "value": "main"},
                        "verified": False,
                    }
                ],
                "tags": ["demo"],
                "zip_url": "https://example.invalid/demo/archive/main.zip",
            }
        ],
    }
    hints_payload = {
        "schema_version": 2,
        "families": {
            "demo": {
                "description": "Routing hints for demo.",
                "strong_keywords": ["demo"],
                "keywords": [],
                "negative_keywords": [],
            }
        },
    }
    _write_json(
        tmp_path / "src" / "fermilink" / "data" / "curated_channels" / "tel-research-group.json",
        curated_payload,
    )
    _write_json(
        tmp_path / "src" / "fermilink" / "data" / "router" / "family_hints.json",
        hints_payload,
    )

    result = subprocess.run(
        [sys.executable, str(_script_path()), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "[ok] Data validation passed." in result.stdout


def test_validate_data_script_fails_when_family_is_missing(tmp_path: Path) -> None:
    curated_payload = {
        "schema_version": 2,
        "channel_id": "tel-research-group",
        "packages": [
            {
                "package_id": "demo",
                "title": "Demo",
                "description": "Demo package",
                "upstream_repo_url": "https://example.invalid/demo",
                "default_version": "branch-head",
                "versions": [
                    {
                        "version_id": "branch-head",
                        "source_archive_url": "https://example.invalid/demo/archive/main.zip",
                        "source_ref": {"type": "branch", "value": "main"},
                        "verified": True,
                    }
                ],
            }
        ],
    }
    hints_payload = {
        "schema_version": 2,
        "families": {
            "other": {
                "description": "Routing hints for other.",
                "strong_keywords": ["other"],
                "keywords": [],
                "negative_keywords": [],
            }
        },
    }
    _write_json(
        tmp_path / "src" / "fermilink" / "data" / "curated_channels" / "tel-research-group.json",
        curated_payload,
    )
    _write_json(
        tmp_path / "src" / "fermilink" / "data" / "router" / "family_hints.json",
        hints_payload,
    )

    result = subprocess.run(
        [sys.executable, str(_script_path()), "--repo-root", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "Missing router family entries for curated packages: demo" in result.stderr
