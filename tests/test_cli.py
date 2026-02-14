from __future__ import annotations

from pathlib import Path

from fermilink import cli
from fermilink.packages.curated_channels import ChannelPackage, ChannelPackageVersion
from fermilink.packages.package_registry import load_registry


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


def test_cli_install_multiple_packages_installs_each_and_syncs_once(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    install_calls: list[dict[str, object]] = []

    def fake_install_from_zip(
        root: Path,
        package_id: str,
        *,
        zip_url: str,
        title: str | None,
        activate: bool,
        force: bool,
        max_zip_bytes: int,
    ) -> dict[str, object]:
        install_calls.append(
            {
                "root": root,
                "package_id": package_id,
                "zip_url": zip_url,
                "title": title,
                "activate": activate,
                "force": force,
                "max_zip_bytes": max_zip_bytes,
            }
        )
        return {"id": package_id}

    monkeypatch.setattr(cli, "install_from_zip", fake_install_from_zip)

    monkeypatch.setattr(
        cli,
        "resolve_curated_package",
        lambda package_id, channel: ChannelPackage(
            package_id=package_id,
            zip_url=f"https://example.invalid/{package_id}.zip",
            title=f"title-{package_id}",
            default_version="branch-head",
            versions=(
                ChannelPackageVersion(
                    version_id="branch-head",
                    source_archive_url=f"https://example.invalid/{package_id}.zip",
                    verified=False,
                ),
            ),
        ),
    )

    sync_calls: list[Path] = []
    monkeypatch.setattr(cli, "sync_router_rules", lambda root: sync_calls.append(root) or {})
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"active_package": "maxwelllink"})

    code = cli.main(["install", "ase", "meep"])
    assert code == 0

    assert [call["package_id"] for call in install_calls] == ["ase", "meep"]
    assert all(call["activate"] is False for call in install_calls)
    assert len(sync_calls) == 1

    output = capsys.readouterr().out
    assert "Installed 2 packages" in output


def test_cli_install_uses_requested_curated_version(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    install_calls: list[dict[str, object]] = []

    def fake_install_from_zip(
        root: Path,
        package_id: str,
        *,
        zip_url: str,
        title: str | None,
        activate: bool,
        force: bool,
        max_zip_bytes: int,
    ) -> dict[str, object]:
        install_calls.append(
            {
                "root": root,
                "package_id": package_id,
                "zip_url": zip_url,
                "title": title,
                "activate": activate,
                "force": force,
                "max_zip_bytes": max_zip_bytes,
            }
        )
        return {"id": package_id}

    monkeypatch.setattr(cli, "install_from_zip", fake_install_from_zip)
    monkeypatch.setattr(
        cli,
        "resolve_curated_package",
        lambda package_id, channel: ChannelPackage(
            package_id=package_id,
            zip_url="https://example.invalid/ase-head.zip",
            title="ASE",
            description="Atomic Simulation Environment",
            default_version="branch-head",
            versions=(
                ChannelPackageVersion(
                    version_id="branch-head",
                    source_archive_url="https://example.invalid/ase-head.zip",
                    source_ref_type="branch",
                    source_ref_value="main",
                    verified=False,
                ),
                ChannelPackageVersion(
                    version_id="v1.0.0",
                    source_archive_url="https://example.invalid/ase-v1.0.0.zip",
                    source_ref_type="tag",
                    source_ref_value="v1.0.0",
                    verified=True,
                ),
            ),
        ),
    )
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {})
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}, "active_package": "ase"})
    monkeypatch.setattr(cli, "save_registry", lambda _root, payload: payload)

    code = cli.main(["install", "ase", "--version", "v1.0.0"])
    assert code == 0
    assert len(install_calls) == 1
    assert install_calls[0]["zip_url"] == "https://example.invalid/ase-v1.0.0.zip"


def test_cli_install_require_verified_rejects_unverified(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    monkeypatch.setattr(
        cli,
        "resolve_curated_package",
        lambda package_id, channel: ChannelPackage(
            package_id=package_id,
            zip_url="https://example.invalid/ase-head.zip",
            title="ASE",
            default_version="branch-head",
            versions=(
                ChannelPackageVersion(
                    version_id="branch-head",
                    source_archive_url="https://example.invalid/ase-head.zip",
                    source_ref_type="branch",
                    source_ref_value="main",
                    verified=False,
                ),
            ),
        ),
    )

    code = cli.main(["install", "ase", "--require-verified"])
    assert code == 2
    err = capsys.readouterr().err
    assert "not verified" in err


def test_cli_install_multiple_packages_rejects_activate(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("SCIPKG_ROOT", str(scipkg_root))

    code = cli.main(["install", "ase", "meep", "--activate"])
    assert code == 2

    err = capsys.readouterr().err
    assert "--activate" in err
