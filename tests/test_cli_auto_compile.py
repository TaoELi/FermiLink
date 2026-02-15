from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli.commands import packages as package_commands


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _base_repo_payloads(repo_root: Path) -> None:
    _write_json(
        repo_root
        / "src"
        / "fermilink"
        / "data"
        / "curated_channels"
        / "tel-research-group.json",
        {
            "schema_version": 2,
            "channel_id": "tel-research-group",
            "packages": [],
        },
    )
    _write_json(
        repo_root / "src" / "fermilink" / "data" / "router" / "family_hints.json",
        {
            "schema_version": 2,
            "families": {},
        },
    )


def _sample_curated_entry(package_id: str) -> dict[str, object]:
    return {
        "package_id": package_id,
        "title": package_id.upper(),
        "description": "Sample package description for testing auto compile metadata merge.",
        "upstream_repo_url": f"https://github.com/example/{package_id}",
        "homepage_url": f"https://github.com/example/{package_id}",
        "zip_url": f"https://github.com/test-user/{package_id}/archive/refs/heads/main.zip",
        "default_version": "branch-head",
        "versions": [
            {
                "version_id": "branch-head",
                "source_archive_url": f"https://github.com/test-user/{package_id}/archive/refs/heads/main.zip",
                "source_ref": {"type": "branch", "value": "main"},
                "verified": False,
            }
        ],
        "tags": ["quantum", package_id],
    }


def _sample_family_entry(package_id: str) -> dict[str, object]:
    return {
        "description": f"Routing hints for {package_id}.",
        "strong_keywords": [package_id, "simulation", "solver", "workflow"],
        "keywords": ["numerics", "modeling", "scientific python", "analysis"],
        "negative_keywords": ["gromacs"],
    }


def test_cli_auto_compile_single_package_success(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "fermilink-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        package_commands, "_ensure_required_commands_available", lambda **_k: None
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex", sandbox_policy="enforce", sandbox_mode="workspace-write"
        ),
    )
    monkeypatch.setattr(package_commands, "_resolve_github_login", lambda: "tester")
    monkeypatch.setattr(
        package_commands,
        "_process_auto_compile_package",
        lambda **kwargs: {
            "status": "ok",
            "package_id": kwargs["package_id"],
            "upstream_repo_url": kwargs["upstream_repo_url"],
        },
    )

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "auto-compile",
            "qutip",
            "https://github.com/qutip/qutip",
            "--fermilink-repo",
            str(repo_root),
            "--dry-run",
            "--json",
        ]
    )
    assert code == 0
    assert payloads
    assert payloads[0]["processed_count"] == 1
    assert payloads[0]["failed_count"] == 0


def test_cli_auto_compile_batch_continues_without_fail_fast(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "fermilink-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    spec_path = tmp_path / "spec.json"
    _write_json(
        spec_path,
        {
            "packages": [
                {
                    "package_id": "badpkg",
                    "upstream_repo_url": "https://github.com/example/badpkg",
                },
                {
                    "package_id": "goodpkg",
                    "upstream_repo_url": "https://github.com/example/goodpkg",
                },
            ]
        },
    )

    monkeypatch.setattr(
        package_commands, "_ensure_required_commands_available", lambda **_k: None
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex", sandbox_policy="enforce", sandbox_mode="workspace-write"
        ),
    )
    monkeypatch.setattr(package_commands, "_resolve_github_login", lambda: "tester")

    def _fake_process(**kwargs):
        if kwargs["package_id"] == "badpkg":
            raise cli.PackageError("forced failure")
        return {"status": "ok", "package_id": kwargs["package_id"]}

    monkeypatch.setattr(
        package_commands, "_process_auto_compile_package", _fake_process
    )
    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "auto-compile",
            "--spec-file",
            str(spec_path),
            "--fermilink-repo",
            str(repo_root),
            "--json",
        ]
    )
    assert code == 2
    assert payloads
    assert payloads[0]["processed_count"] == 1
    assert payloads[0]["failed_count"] == 1


def test_cli_auto_compile_fail_fast_stops_after_first_failure(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "fermilink-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    spec_path = tmp_path / "spec.json"
    _write_json(
        spec_path,
        {
            "packages": [
                {
                    "package_id": "badpkg",
                    "upstream_repo_url": "https://github.com/example/badpkg",
                },
                {
                    "package_id": "goodpkg",
                    "upstream_repo_url": "https://github.com/example/goodpkg",
                },
            ]
        },
    )

    monkeypatch.setattr(
        package_commands, "_ensure_required_commands_available", lambda **_k: None
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex", sandbox_policy="enforce", sandbox_mode="workspace-write"
        ),
    )
    monkeypatch.setattr(package_commands, "_resolve_github_login", lambda: "tester")

    calls: list[str] = []

    def _fake_process(**kwargs):
        calls.append(kwargs["package_id"])
        raise cli.PackageError("forced failure")

    monkeypatch.setattr(
        package_commands, "_process_auto_compile_package", _fake_process
    )
    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "auto-compile",
            "--spec-file",
            str(spec_path),
            "--fermilink-repo",
            str(repo_root),
            "--fail-fast",
            "--json",
        ]
    )
    assert code == 2
    assert calls == ["badpkg"]
    assert payloads
    assert payloads[0]["processed_count"] == 0
    assert payloads[0]["failed_count"] == 1


def test_cli_auto_compile_requires_codex_provider(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo_root = tmp_path / "fermilink-repo"
    repo_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        package_commands, "_ensure_required_commands_available", lambda **_k: None
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="claude", sandbox_policy="enforce", sandbox_mode="workspace-write"
        ),
    )

    code = cli.main(
        [
            "auto-compile",
            "qutip",
            "https://github.com/qutip/qutip",
            "--fermilink-repo",
            str(repo_root),
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "requires Codex provider" in err


def test_merge_metadata_entries_writes_payloads(monkeypatch, tmp_path: Path) -> None:
    repo_root = tmp_path / "fermilink-repo"
    _base_repo_payloads(repo_root)
    monkeypatch.setattr(
        package_commands,
        "_validate_data_payloads_with_script",
        lambda **_k: None,
    )

    result = package_commands._merge_metadata_entries(
        fermilink_repo=repo_root,
        channel_id="tel-research-group",
        package_id="qutip",
        curated_entry=_sample_curated_entry("qutip"),
        family_entry=_sample_family_entry("qutip"),
        update_existing=False,
        dry_run=False,
    )
    assert result["replaced_curated"] is False
    assert result["replaced_family"] is False
    curated_payload = json.loads(
        (
            repo_root
            / "src"
            / "fermilink"
            / "data"
            / "curated_channels"
            / "tel-research-group.json"
        ).read_text(encoding="utf-8")
    )
    package_ids = [item["package_id"] for item in curated_payload["packages"]]
    assert "qutip" in package_ids
    family_payload = json.loads(
        (
            repo_root / "src" / "fermilink" / "data" / "router" / "family_hints.json"
        ).read_text(encoding="utf-8")
    )
    assert "qutip" in family_payload["families"]


def test_merge_metadata_entries_blocks_duplicate_without_update(
    monkeypatch, tmp_path: Path
) -> None:
    repo_root = tmp_path / "fermilink-repo"
    _base_repo_payloads(repo_root)
    existing_curated = _sample_curated_entry("qutip")
    existing_family = _sample_family_entry("qutip")
    _write_json(
        repo_root
        / "src"
        / "fermilink"
        / "data"
        / "curated_channels"
        / "tel-research-group.json",
        {
            "schema_version": 2,
            "channel_id": "tel-research-group",
            "packages": [existing_curated],
        },
    )
    _write_json(
        repo_root / "src" / "fermilink" / "data" / "router" / "family_hints.json",
        {
            "schema_version": 2,
            "families": {"qutip": existing_family},
        },
    )
    monkeypatch.setattr(
        package_commands,
        "_validate_data_payloads_with_script",
        lambda **_k: None,
    )

    with pytest.raises(cli.PackageError):
        package_commands._merge_metadata_entries(
            fermilink_repo=repo_root,
            channel_id="tel-research-group",
            package_id="qutip",
            curated_entry=_sample_curated_entry("qutip"),
            family_entry=_sample_family_entry("qutip"),
            update_existing=False,
            dry_run=False,
        )
