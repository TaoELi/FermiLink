from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli import zero_arg
from fermilink.packages.curated_channels import ChannelPackage, ChannelPackageVersion
from fermilink.packages.package_registry import PACKAGE_WORKFLOW_TYPE_KEY, load_registry


def _make_local_package(path: Path) -> None:
    _make_local_package_with_entries(path, ["skills"])


def _make_local_package_with_entries(path: Path, entries: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        (path / entry).mkdir(parents=True, exist_ok=True)
        (path / entry / "README.md").write_text(entry, encoding="utf-8")


def test_top_level_module_entrypoint_runs_cli_help() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src"
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(src_root)
        if not existing_pythonpath
        else os.pathsep.join([str(src_root), existing_pythonpath])
    )

    completed = subprocess.run(
        [sys.executable, "-m", "fermilink", "--help"],
        cwd=str(repo_root),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "usage: fermilink" in completed.stdout
    assert "Unified FermiLink CLI" in completed.stdout


def test_cli_install_local_auto_sync(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "ase-src"
    _make_local_package(source)

    code = cli.main(["install", "ase", "--local-path", str(source), "--activate"])
    assert code == 0

    registry = load_registry(scipkg_root)
    assert registry["active_package"] == "ase"
    assert registry["packages"]["ase"][PACKAGE_WORKFLOW_TYPE_KEY] == "simulation"
    assert (scipkg_root / "router_rules.json").exists()


def test_cli_install_local_records_workflow_type(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "experiment-src"
    _make_local_package(source)

    code = cli.main(
        [
            "install",
            "experimentpkg",
            "--local-path",
            str(source),
            "--workflow-type",
            "experiment",
        ]
    )
    assert code == 0

    registry = load_registry(scipkg_root)
    assert registry["packages"]["experimentpkg"][PACKAGE_WORKFLOW_TYPE_KEY] == (
        "experiment"
    )


def test_cli_install_infers_local_path_package_id(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "mos2-quantum-transport-skill"
    _make_local_package(source)

    code = cli.main(["install", str(source), "--no-router-sync"])
    assert code == 0

    registry = load_registry(scipkg_root)
    meta = registry["packages"]["mos2-quantum-transport-skill"]
    assert meta["source"] == f"local-path:{source.resolve()}"


def test_cli_install_rejects_empty_inferred_local_path(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "empty-package"
    source.mkdir()

    code = cli.main(["install", str(source)])
    assert code == 2

    err = capsys.readouterr().err
    assert "not an existing non-empty directory" in err


def test_cli_install_infers_github_repo_url(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    install_calls: list[dict[str, object]] = []

    def fake_install_from_git_url(
        root: Path,
        package_id: str,
        *,
        git_url: str,
        title: str | None,
        activate: bool,
        force: bool,
        workflow_type: str,
    ) -> dict[str, object]:
        install_calls.append(
            {
                "root": root,
                "package_id": package_id,
                "git_url": git_url,
                "title": title,
                "activate": activate,
                "force": force,
                "workflow_type": workflow_type,
            }
        )
        return {
            "id": package_id,
            "source": "git:https://github.com/TaoELi/mos2-quantum-transport-skill",
        }

    monkeypatch.setattr(cli, "install_from_git_url", fake_install_from_git_url)

    code = cli.main(
        [
            "install",
            "https://github.com/TaoELi/mos2-quantum-transport-skill",
            "--workflow-type",
            "experiment",
            "--activate",
            "--no-router-sync",
        ]
    )
    assert code == 0
    assert install_calls == [
        {
            "root": scipkg_root,
            "package_id": "mos2-quantum-transport-skill",
            "git_url": "https://github.com/TaoELi/mos2-quantum-transport-skill",
            "title": None,
            "activate": True,
            "force": False,
            "workflow_type": "experiment",
        }
    ]


def test_cli_install_bare_local_path_falls_back_after_curated_miss(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))
    monkeypatch.chdir(tmp_path)

    source = tmp_path / "custom-skill"
    _make_local_package(source)

    code = cli.main(["install", "custom-skill", "--no-router-sync"])
    assert code == 0

    registry = load_registry(scipkg_root)
    assert registry["packages"]["custom-skill"]["source"] == (
        f"local-path:{source.resolve()}"
    )


def test_cli_install_curated_id_wins_over_same_named_local_dir(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))
    monkeypatch.chdir(tmp_path)
    _make_local_package(tmp_path / "ase")

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
        workflow_type: str,
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
                "workflow_type": workflow_type,
            }
        )
        return {"id": package_id}

    monkeypatch.setattr(cli, "install_from_zip", fake_install_from_zip)
    monkeypatch.setattr(
        cli,
        "resolve_curated_package",
        lambda package_id, channel: ChannelPackage(
            package_id=package_id,
            zip_url="https://example.invalid/ase.zip",
            title="ASE",
            default_version="branch-head",
            versions=(
                ChannelPackageVersion(
                    version_id="branch-head",
                    source_archive_url="https://example.invalid/ase.zip",
                    verified=True,
                ),
            ),
        ),
    )
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {})
    monkeypatch.setattr(
        cli, "load_registry", lambda _root: {"packages": {}, "active_package": "ase"}
    )
    monkeypatch.setattr(cli, "save_registry", lambda _root, payload: payload)

    code = cli.main(["install", "ase"])
    assert code == 0
    assert install_calls[0]["zip_url"] == "https://example.invalid/ase.zip"


def test_cli_dependencies(monkeypatch, tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

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


def test_cli_overlay_remove_from_configured_entries(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "overlay-src"
    _make_local_package_with_entries(source, ["skills", "docs"])

    assert cli.main(["install", "overlaypkg", "--local-path", str(source)]) == 0
    assert (
        cli.main(
            ["overlay", "overlaypkg", "--entries", "skills,docs"],
        )
        == 0
    )
    assert cli.main(["overlay", "overlaypkg", "--remove", "skills"]) == 0

    registry = load_registry(scipkg_root)
    assert registry["packages"]["overlaypkg"].get("overlay_entries") == ["docs"]


def test_cli_overlay_remove_from_default_exportable_entries(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "overlay-src"
    _make_local_package_with_entries(source, ["skills", "docs"])

    assert cli.main(["install", "overlaypkg", "--local-path", str(source)]) == 0
    assert cli.main(["overlay", "overlaypkg", "--remove", "skills"]) == 0

    registry = load_registry(scipkg_root)
    assert registry["packages"]["overlaypkg"].get("overlay_entries") == ["docs"]


def test_cli_overlay_remove_rejects_combined_entry_flags(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    source = tmp_path / "overlay-src"
    _make_local_package_with_entries(source, ["skills", "docs"])

    assert cli.main(["install", "overlaypkg", "--local-path", str(source)]) == 0
    code = cli.main(["overlay", "overlaypkg", "--entry", "skills", "--remove", "docs"])

    assert code == 2
    err = capsys.readouterr().err
    assert "Cannot combine --remove with --entry/--entries." in err


def test_cli_install_multiple_packages_installs_each_and_syncs_once(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

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
        workflow_type: str,
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
                "workflow_type": workflow_type,
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
    monkeypatch.setattr(
        cli, "sync_router_rules", lambda root: sync_calls.append(root) or {}
    )
    monkeypatch.setattr(
        cli, "load_registry", lambda _root: {"active_package": "maxwelllink"}
    )

    code = cli.main(["install", "ase", "meep"])
    assert code == 0

    assert [call["package_id"] for call in install_calls] == ["ase", "meep"]
    assert all(call["activate"] is False for call in install_calls)
    assert all(call["workflow_type"] == "simulation" for call in install_calls)
    assert len(sync_calls) == 1

    output = capsys.readouterr().out
    assert "Installed 2 packages" in output


def test_cli_install_uses_requested_curated_version(
    monkeypatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

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
        workflow_type: str,
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
                "workflow_type": workflow_type,
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
    monkeypatch.setattr(
        cli, "load_registry", lambda _root: {"packages": {}, "active_package": "ase"}
    )
    monkeypatch.setattr(cli, "save_registry", lambda _root, payload: payload)

    code = cli.main(["install", "ase", "--version", "v1.0.0"])
    assert code == 0
    assert len(install_calls) == 1
    assert install_calls[0]["zip_url"] == "https://example.invalid/ase-v1.0.0.zip"


def test_cli_install_require_verified_rejects_unverified(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

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
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    code = cli.main(["install", "ase", "meep", "--activate"])
    assert code == 2

    err = capsys.readouterr().err
    assert "--activate" in err


def _make_zero_arg_state(
    *,
    selected_provider: str | None = "codex",
    provider_setup_needed: bool = False,
    has_packages: bool = True,
) -> dict[str, object]:
    return {
        "runtime_policy": AgentRuntimePolicy(provider="codex"),
        "selected_provider": selected_provider,
        "provider_setup_needed": provider_setup_needed,
        "provider_scan": {
            "codex": {
                "binary_found": selected_provider == "codex",
                "auth_state": "ready",
            },
            "claude": {"binary_found": False, "auth_state": "missing"},
            "gemini": {"binary_found": False, "auth_state": "missing"},
            "deepseek": {"binary_found": False, "auth_state": "missing"},
        },
        "packages": {
            "count": 1 if has_packages else 0,
            "has_packages": has_packages,
            "active_package": "maxwelllink" if has_packages else None,
            "scipkg_root": "/tmp/scientific_packages",
        },
        "services": {
            "runner": {"running": False},
            "web": {"running": False},
        },
        "telegram": {
            "token_present": False,
            "allowlist_present": False,
        },
        "hpc": {
            "profile_path": None,
            "profile_valid": False,
            "profile_error": None,
            "slurm_submit_available": False,
            "slurm_wait_available": False,
        },
    }


def test_cli_no_args_noninteractive_prints_status(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))
    monkeypatch.setattr(zero_arg, "_interactive_tty", lambda: False)
    monkeypatch.setattr(
        zero_arg, "_probe_zero_arg_state", lambda: _make_zero_arg_state()
    )

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "FermiLink Status" in out
    assert "| Component" in out
    assert "Web UI" in out
    assert "Run in an interactive terminal for guided setup" in out


def test_cli_no_args_interactive_quit_from_main_menu(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))
    monkeypatch.setattr(zero_arg, "_interactive_tty", lambda: True)
    monkeypatch.setattr(
        zero_arg, "_probe_zero_arg_state", lambda: _make_zero_arg_state()
    )
    answers = iter(["10"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert "FFFFF  EEEEE  RRRR" in out
    assert "FermiLink Status" in out
    assert "Run a simulation" in out
    assert "Advanced: Compile a local package for FermiLink" in out
    assert "Show system status" in out


def test_cli_no_args_runs_provider_setup_before_menu(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))
    monkeypatch.setattr(zero_arg, "_interactive_tty", lambda: True)
    states = iter(
        [
            _make_zero_arg_state(selected_provider=None, provider_setup_needed=True),
            _make_zero_arg_state(
                selected_provider="codex", provider_setup_needed=False
            ),
            _make_zero_arg_state(
                selected_provider="codex", provider_setup_needed=False
            ),
        ]
    )
    monkeypatch.setattr(zero_arg, "_probe_zero_arg_state", lambda: next(states))
    calls: list[str] = []
    monkeypatch.setattr(
        zero_arg,
        "_run_zero_arg_provider_setup",
        lambda state: calls.append(str(state.get("selected_provider"))),
    )
    answers = iter(["10"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = cli.main([])

    assert code == 0
    assert calls == ["None"]


def test_cli_no_args_hero_banner_shows_on_each_invocation(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))
    monkeypatch.setattr(zero_arg, "_interactive_tty", lambda: True)
    monkeypatch.setattr(
        zero_arg, "_probe_zero_arg_state", lambda: _make_zero_arg_state()
    )

    answers_first = iter(["10"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers_first))
    first_code = cli.main([])
    first_out = capsys.readouterr().out

    answers_second = iter(["10"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers_second))
    second_code = cli.main([])
    second_out = capsys.readouterr().out

    assert first_code == 0
    assert second_code == 0
    assert "FFFFF  EEEEE  RRRR" in first_out
    assert "FFFFF  EEEEE  RRRR" in second_out


def test_cli_no_args_hero_banner_only_once_within_single_run(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    monkeypatch.setenv("FERMILINK_HOME", str(tmp_path / ".fermilink"))
    monkeypatch.setattr(zero_arg, "_interactive_tty", lambda: True)
    monkeypatch.setattr(
        zero_arg, "_probe_zero_arg_state", lambda: _make_zero_arg_state()
    )
    answers = iter(["9", "10"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = cli.main([])

    assert code == 0
    out = capsys.readouterr().out
    assert out.count("FFFFF  EEEEE  RRRR") == 1


def test_zero_arg_provider_status_row_is_concise() -> None:
    state = _make_zero_arg_state()
    state["provider_scan"] = {
        "codex": {"binary_found": True, "auth_state": "unknown"},
        "claude": {"binary_found": True, "auth_state": "unknown"},
        "gemini": {"binary_found": True, "auth_state": "unknown"},
        "deepseek": {"binary_found": True, "auth_state": "unknown"},
    }

    row = zero_arg._zero_arg_provider_status_row(state)

    assert row == (
        "Providers",
        "codex pending",
        "default=codex; detected=codex, claude, gemini, deepseek",
    )


def test_zero_arg_compile_setup_executes_compile_command(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "local-package"
    project_root.mkdir()
    state = _make_zero_arg_state()
    executed: list[list[str]] = []
    monkeypatch.setattr(
        zero_arg,
        "_execute_cli_argv",
        lambda argv: executed.append(argv) or 0,
    )
    monkeypatch.setattr(
        zero_arg,
        "_confirm_provider_login_if_needed",
        lambda _state: True,
    )
    answers = iter(
        [
            "my-local-package",
            str(project_root),
            "n",
            "y",
            "y",
            "y",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = zero_arg._run_zero_arg_compile_setup(state)

    assert code == 0
    assert executed == [
        [
            "compile",
            "my-local-package",
            str(project_root),
            "--docs-only",
            "--strict-compile-validation",
            "--activate",
        ]
    ]


def test_zero_arg_recompile_setup_executes_paper_mode_command(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "package-dev"
    project_root.mkdir()
    doc_path = tmp_path / "workflow.md"
    doc_path.write_text("# workflow", encoding="utf-8")
    data_dir = tmp_path / "supplementary"
    data_dir.mkdir()
    state = _make_zero_arg_state()
    executed: list[list[str]] = []
    monkeypatch.setattr(
        zero_arg,
        "_execute_cli_argv",
        lambda argv: executed.append(argv) or 0,
    )
    monkeypatch.setattr(
        zero_arg,
        "_confirm_provider_login_if_needed",
        lambda _state: True,
    )
    answers = iter(
        [
            "mypkg",
            str(project_root),
            "1",
            str(doc_path),
            str(data_dir),
            "focus on spectra and validation",
            "y",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = zero_arg._run_zero_arg_recompile_setup(state)

    assert code == 0
    assert executed == [
        [
            "recompile",
            "mypkg",
            str(project_root),
            "--doc",
            str(doc_path),
            "--data-dir",
            str(data_dir),
            "--comment",
            "focus on spectra and validation",
            "--activate",
        ]
    ]


def test_zero_arg_recompile_setup_executes_memory_mode_command(
    monkeypatch, tmp_path: Path
) -> None:
    memory_root = tmp_path / "projects"
    memory_root.mkdir()
    state = _make_zero_arg_state()
    executed: list[list[str]] = []
    monkeypatch.setattr(
        zero_arg,
        "_execute_cli_argv",
        lambda argv: executed.append(argv) or 0,
    )
    monkeypatch.setattr(
        zero_arg,
        "_confirm_provider_login_if_needed",
        lambda _state: True,
    )
    answers = iter(
        [
            "mypkg",
            "",
            "2",
            str(memory_root),
            "2",
            "y",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))

    code = zero_arg._run_zero_arg_recompile_setup(state)

    assert code == 0
    assert executed == [
        [
            "recompile",
            "mypkg",
            "--memory",
            str(memory_root),
            "--memory-scope",
            "package-specific",
        ]
    ]


def test_zero_arg_mode_inference_prefers_reproduce_then_research_then_loop() -> None:
    assert (
        zero_arg._infer_zero_arg_mode("reproduce figures 1-4 from paper.pdf")
        == "reproduce"
    )
    assert (
        zero_arg._infer_zero_arg_mode("design and optimize a cavity QED workflow")
        == "research"
    )
    assert (
        zero_arg._infer_zero_arg_mode("monitor a long-running slurm job overnight")
        == "loop"
    )
    assert zero_arg._infer_zero_arg_mode("simulate a single cavity mode") == "exec"


def test_zero_arg_hpc_probe_does_not_require_cli_resolve_fermilink_home(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("FERMILINK_DEFAULT_HPC_PROFILE", raising=False)
    monkeypatch.delattr(cli, "resolve_fermilink_home", raising=False)
    monkeypatch.setattr(cli, "_resolve_project_path", lambda raw: Path(raw).resolve())
    monkeypatch.setattr(
        zero_arg,
        "resolve_fermilink_home",
        lambda: tmp_path / ".fermilink",
    )

    state = zero_arg._probe_zero_arg_hpc_state()

    assert state["profile_path"] is None
    assert state["profile_valid"] is False
