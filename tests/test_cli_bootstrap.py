from __future__ import annotations

from pathlib import Path

from fermilink import cli
from fermilink.packages.curated_channels import ChannelPackage


def test_bootstrap_skips_when_packages_exist(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {"packages": {"ase": {"id": "ase"}}},
    )
    monkeypatch.setattr(
        cli,
        "install_from_zip",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not install")),
    )

    result = cli._ensure_bootstrap_package_for_services()
    assert result["status"] == "skipped"
    assert result["reason"] == "packages_present"
    assert result["package_count"] == 1


def test_bootstrap_installs_maxwelllink_when_registry_empty(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})

    called: dict[str, object] = {}

    def fake_resolve_curated(
        package_id: str, *, channel: str | None = None
    ) -> ChannelPackage:
        called["resolve_package_id"] = package_id
        called["resolve_channel"] = channel
        return ChannelPackage(
            package_id="maxwelllink",
            title="MaxwellLink",
            zip_url="https://example.invalid/maxwelllink.zip",
        )

    def fake_install(
        root: Path,
        package_id: str,
        *,
        zip_url: str,
        title: str | None = None,
        activate: bool = False,
        force: bool = False,
        max_zip_bytes: int = 0,
    ) -> dict[str, object]:
        called["install_root"] = root
        called["install_package_id"] = package_id
        called["install_zip_url"] = zip_url
        called["install_title"] = title
        called["install_activate"] = activate
        called["install_force"] = force
        called["install_max_zip_bytes"] = max_zip_bytes
        return {"id": package_id}

    monkeypatch.setattr(cli, "resolve_curated_package", fake_resolve_curated)
    monkeypatch.setattr(cli, "install_from_zip", fake_install)
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {"updated": True})

    result = cli._ensure_bootstrap_package_for_services()
    assert result["status"] == "installed"
    assert result["package_id"] == "maxwelllink"
    assert result["installed"]["id"] == "maxwelllink"
    assert called["resolve_package_id"] == "maxwelllink"
    assert called["resolve_channel"] == "skilled-scipkg"
    assert called["install_root"] == scipkg_root
    assert called["install_activate"] is True
    assert called["install_force"] is False
    assert "warning" in result

    captured = capsys.readouterr()
    assert "No scientific package is installed yet" in captured.err


def test_start_continues_when_bootstrap_fails(monkeypatch, tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    specs = {"runner": object(), "web": object()}
    names = ["runner", "web"]
    payloads: list[dict[str, object]] = []

    monkeypatch.setattr(cli, "resolve_runtime_root", lambda: runtime_root)
    monkeypatch.setattr(cli, "_resolve_specs", lambda _components: (names, specs))
    monkeypatch.setattr(
        cli,
        "_ensure_bootstrap_package_for_services",
        lambda: {
            "status": "failed",
            "package_id": "maxwelllink",
            "error": "download failed",
        },
    )
    monkeypatch.setattr(
        cli,
        "start_service",
        lambda _runtime_root, spec: {
            "service": "runner" if spec is specs["runner"] else "web",
            "status": "started",
        },
    )
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(["start", "--json"])
    assert code == 0
    assert payloads
    assert payloads[0]["bootstrap"]["status"] == "failed"
    results = payloads[0]["results"]
    assert isinstance(results, list)
    assert [item["service"] for item in results] == ["runner", "web"]


def test_install_parser_accepts_active_alias() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "install",
            "maxwelllink",
            "--zip-url",
            "https://example.invalid/pkg.zip",
            "--active",
        ]
    )
    assert args.activate is True
