from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from fermilink import package_registry
from fermilink.runner import scientific_packages


def _make_package(path: Path, entries: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        entry_dir = path / entry
        entry_dir.mkdir(parents=True, exist_ok=True)
        (entry_dir / "README.md").write_text(f"{entry}\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("MaxwellLink", "maxwelllink"),
        (" meep-main ", "meep-main"),
        ("psi4_v2", "psi4_v2"),
        ("A/B C", "a-b-c"),
    ],
)
def test_normalize_package_id_contract_matches_runner(raw: str, expected: str) -> None:
    assert package_registry.normalize_package_id(raw) == expected
    assert scientific_packages.normalize_package_id(raw) == expected


def test_normalize_package_id_error_contract_matches_runner() -> None:
    with pytest.raises(package_registry.PackageValidationError) as cli_exc:
        package_registry.normalize_package_id("!!!")

    with pytest.raises(scientific_packages.PackageValidationError) as runner_exc:
        scientific_packages.normalize_package_id("!!!")

    assert str(cli_exc.value) == "Package id is empty after normalization."
    assert str(runner_exc.value) == str(cli_exc.value)


def test_default_registry_shape_contract_matches_runner() -> None:
    cli_default = package_registry._default_registry()
    runner_default = scientific_packages._default_registry()

    assert set(cli_default.keys()) == set(runner_default.keys()) == {
        "version",
        "active_package",
        "packages",
        "updated_at",
    }
    assert cli_default["version"] == runner_default["version"] == 1
    assert cli_default["active_package"] is None
    assert runner_default["active_package"] is None
    assert cli_default["packages"] == {}
    assert runner_default["packages"] == {}
    assert isinstance(cli_default["updated_at"], str) and cli_default["updated_at"]
    assert isinstance(runner_default["updated_at"], str) and runner_default["updated_at"]


@pytest.mark.parametrize(
    ("entry_name", "expected"),
    [
        ("skills", True),
        ("docs", True),
        (".git", False),
        ("__pycache__", False),
        ("node_modules", False),
        (".hidden", False),
        ("agents.md", False),
    ],
)
def test_entry_exportability_contract_matches_runner(entry_name: str, expected: bool) -> None:
    entry = Path(entry_name)
    assert package_registry._entry_is_exportable(entry) is expected
    assert scientific_packages._entry_is_exportable(entry) is expected


def test_manifest_extractors_contract_matches_runner() -> None:
    manifest = {
        "linked_entries": [
            {"name": "skills"},
            "docs",
            {"name": ""},
            {"foo": "bar"},
            123,
        ],
        "linked_dependency_packages": [
            {"package_id": "meep"},
            {"name": "QUTIP"},
            {"package_id": "!!!"},
            "psi4",
            {"name": ""},
            123,
        ],
    }

    expected_entries = {"skills", "docs"}
    expected_dependency_ids = {"meep", "qutip", "psi4"}

    assert package_registry._manifest_entry_names(manifest) == expected_entries
    assert scientific_packages._manifest_entry_names(manifest) == expected_entries
    assert (
        package_registry._manifest_dependency_ids(manifest)
        == expected_dependency_ids
    )
    assert (
        scientific_packages._manifest_dependency_ids(manifest)
        == expected_dependency_ids
    )


def test_registry_normalization_active_fallback_contract() -> None:
    payload = {
        "packages": {
            "b_pkg": {},
            "a_pkg": {},
        },
        "active_package": "missing",
    }

    cli_normalized = package_registry._normalize_registry(payload)
    runner_normalized = scientific_packages._normalize_registry(payload)

    assert cli_normalized["active_package"] == "a_pkg"
    assert runner_normalized["active_package"] is None


def test_registry_normalization_non_dict_meta_contract() -> None:
    payload = {
        "packages": {
            "alpha": "not-a-dict",
        },
    }

    cli_normalized = package_registry._normalize_registry(payload)
    runner_normalized = scientific_packages._normalize_registry(payload)

    assert "alpha" in cli_normalized["packages"]
    assert cli_normalized["packages"]["alpha"]["id"] == "alpha"
    assert runner_normalized["packages"] == {}


def test_registry_normalization_dependency_field_contract() -> None:
    payload = {
        "packages": {
            "owner": {
                "dependency_package_ids": ["qutip", "owner", "!!!", "qutip"],
            },
        },
    }

    cli_normalized = package_registry._normalize_registry(payload)
    runner_normalized = scientific_packages._normalize_registry(payload)

    assert cli_normalized["packages"]["owner"]["dependency_package_ids"] == [
        "qutip",
        "owner",
        "!!!",
        "qutip",
    ]
    assert "dependency_package_ids" not in runner_normalized["packages"]["owner"]


def test_overlay_manifest_payload_shape_contract_matches_runner(tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    _make_package(package_root, ["skills", "docs"])
    package_meta = {
        "installed_path": str(package_root),
        "overlay_entries": ["skills"],
        "dependency_package_ids": None,
    }

    cli_workspace = tmp_path / "cli" / "workspace"
    cli_repo = cli_workspace / "repo"
    cli_repo.mkdir(parents=True, exist_ok=True)
    cli_summary = package_registry.overlay_package_into_repo(
        repo_dir=cli_repo,
        workspace_root=cli_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "cli" / "scipkg",
    )
    cli_manifest = package_registry.load_workspace_manifest(cli_workspace)

    runner_workspace = tmp_path / "runner" / "workspace"
    runner_repo = runner_workspace / "repo"
    runner_repo.mkdir(parents=True, exist_ok=True)
    runner_summary = scientific_packages.overlay_package_into_repo(
        repo_dir=runner_repo,
        workspace_root=runner_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "runner" / "scipkg",
    )
    runner_manifest = scientific_packages.load_workspace_manifest(runner_workspace)

    assert cli_manifest is not None
    assert runner_manifest is not None
    assert set(cli_summary.keys()) == set(runner_summary.keys())
    assert set(cli_manifest.keys()) == set(runner_manifest.keys())
    assert cli_summary["package_id"] == runner_summary["package_id"] == "demo"
    assert cli_summary["requested_entries"] == runner_summary["requested_entries"] == [
        "skills"
    ]
    assert cli_summary["dependency_package_ids"] == runner_summary["dependency_package_ids"] == []
    assert cli_summary["missing_requested_entries"] == runner_summary["missing_requested_entries"] == []
    assert cli_manifest["configured_dependency_package_ids"] == runner_manifest[
        "configured_dependency_package_ids"
    ] == []
    assert {item["name"] for item in cli_manifest["linked_entries"]} == {"skills"}
    assert {item["name"] for item in runner_manifest["linked_entries"]} == {"skills"}


def test_overlay_previous_non_symlink_collision_behavior_contract(tmp_path: Path) -> None:
    package_root = tmp_path / "pkg"
    _make_package(package_root, ["skills"])
    package_meta = {"installed_path": str(package_root), "overlay_entries": ["skills"]}

    cli_workspace = tmp_path / "cli" / "workspace"
    cli_repo = cli_workspace / "repo"
    cli_repo.mkdir(parents=True, exist_ok=True)
    package_registry.overlay_package_into_repo(
        repo_dir=cli_repo,
        workspace_root=cli_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "cli" / "scipkg",
    )
    cli_target = cli_repo / "skills"
    if cli_target.is_symlink() or cli_target.is_file():
        cli_target.unlink(missing_ok=True)
    elif cli_target.is_dir():
        shutil.rmtree(cli_target, ignore_errors=True)
    cli_target.mkdir(parents=True, exist_ok=True)
    (cli_target / "USER.txt").write_text("user-owned", encoding="utf-8")
    cli_second = package_registry.overlay_package_into_repo(
        repo_dir=cli_repo,
        workspace_root=cli_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "cli" / "scipkg",
        allow_replace_existing=False,
    )

    runner_workspace = tmp_path / "runner" / "workspace"
    runner_repo = runner_workspace / "repo"
    runner_repo.mkdir(parents=True, exist_ok=True)
    scientific_packages.overlay_package_into_repo(
        repo_dir=runner_repo,
        workspace_root=runner_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "runner" / "scipkg",
    )
    runner_target = runner_repo / "skills"
    if runner_target.is_symlink() or runner_target.is_file():
        runner_target.unlink(missing_ok=True)
    elif runner_target.is_dir():
        shutil.rmtree(runner_target, ignore_errors=True)
    runner_target.mkdir(parents=True, exist_ok=True)
    (runner_target / "USER.txt").write_text("user-owned", encoding="utf-8")
    runner_second = scientific_packages.overlay_package_into_repo(
        repo_dir=runner_repo,
        workspace_root=runner_workspace,
        package_id="demo",
        package_meta=package_meta,
        scipkg_root=tmp_path / "runner" / "scipkg",
        allow_replace_existing=False,
    )

    assert cli_second["collision_count"] == 0
    assert runner_second["collision_count"] == 1
    assert runner_second["collisions"] == ["skills"]


def test_dependency_root_collision_contract_matches_runner(tmp_path: Path) -> None:
    main_root = tmp_path / "main"
    dep_root = tmp_path / "dep"
    _make_package(main_root, ["skills"])
    _make_package(dep_root, ["meep"])

    cli_scipkg = tmp_path / "cli" / "scipkg"
    package_registry.register_package(
        cli_scipkg,
        "main",
        installed_path=main_root,
        source="local-main",
    )
    package_registry.register_package(
        cli_scipkg,
        "dep",
        installed_path=dep_root,
        source="local-dep",
    )
    package_registry.set_package_dependency_ids(cli_scipkg, "main", ["dep"])
    cli_meta = package_registry.load_registry(cli_scipkg)["packages"]["main"]
    cli_workspace = tmp_path / "cli" / "workspace"
    cli_repo = cli_workspace / "repo"
    cli_repo.mkdir(parents=True, exist_ok=True)
    dependency_root_file = cli_repo / package_registry.PACKAGE_DEPENDENCIES_DIRNAME
    dependency_root_file.write_text("not a directory", encoding="utf-8")
    cli_result = package_registry.overlay_package_into_repo(
        repo_dir=cli_repo,
        workspace_root=cli_workspace,
        package_id="main",
        package_meta=cli_meta,
        scipkg_root=cli_scipkg,
        allow_replace_existing=False,
    )

    runner_scipkg = tmp_path / "runner" / "scipkg"
    scientific_packages.register_package(
        runner_scipkg,
        "main",
        installed_path=main_root,
        source="local-main",
    )
    scientific_packages.register_package(
        runner_scipkg,
        "dep",
        installed_path=dep_root,
        source="local-dep",
    )
    scientific_packages.set_package_dependency_ids(runner_scipkg, "main", ["dep"])
    runner_meta = scientific_packages.load_registry(runner_scipkg)["packages"]["main"]
    runner_workspace = tmp_path / "runner" / "workspace"
    runner_repo = runner_workspace / "repo"
    runner_repo.mkdir(parents=True, exist_ok=True)
    (runner_repo / scientific_packages.PACKAGE_DEPENDENCIES_DIRNAME).write_text(
        "not a directory",
        encoding="utf-8",
    )
    runner_result = scientific_packages.overlay_package_into_repo(
        repo_dir=runner_repo,
        workspace_root=runner_workspace,
        package_id="main",
        package_meta=runner_meta,
        scipkg_root=runner_scipkg,
        allow_replace_existing=False,
    )

    assert cli_result["dependency_collision_count"] == 1
    assert runner_result["dependency_collision_count"] == 1
    assert cli_result["linked_dependency_count"] == 0
    assert runner_result["linked_dependency_count"] == 0
    assert cli_result["dependency_collisions"] == ["dep"]
    assert runner_result["dependency_collisions"] == ["dep"]
