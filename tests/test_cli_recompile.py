from __future__ import annotations

from pathlib import Path

from fermilink import cli


def _make_tool_source(path: Path) -> None:
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "references").mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("skill", encoding="utf-8")
    (path / "scripts" / "generate_skills_folder.py").write_text(
        "print('ok')\n", encoding="utf-8"
    )


def _default_profile() -> dict[str, object]:
    return {
        "package_name": "newpkg",
        "docs_only": False,
        "docs_dirs": ["docs"],
        "tutorial_dirs": ["examples"],
        "test_dirs": ["tests"],
        "source_dirs": ["src"],
        "profile_source": "file",
        "profile_path": "skills/.compile_profile.json",
        "warnings": [],
    }


def _make_existing_skills(project_root: Path) -> None:
    (project_root / "skills" / "newpkg-index").mkdir(parents=True, exist_ok=True)
    (project_root / "skills" / "newpkg-index" / "SKILL.md").write_text(
        "index", encoding="utf-8"
    )


def test_recompile_requires_existing_skills_folder(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})

    code = cli.main(["recompile", "newpkg", str(project_root)])
    assert code == 2
    err = capsys.readouterr().err
    assert "requires an existing skills/ folder" in err


def test_recompile_install_off_skips_registry_and_install(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)

    def _fail(*_a, **_k):
        raise AssertionError("unexpected install/registry call")

    monkeypatch.setattr(cli, "resolve_scipkg_root", _fail)
    monkeypatch.setattr(cli, "load_registry", _fail)
    monkeypatch.setattr(cli, "install_from_local_path", _fail)
    monkeypatch.setattr(cli, "sync_router_rules", _fail)
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": 1,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence"},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 13,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--install-off",
            "--json",
        ]
    )
    assert code == 0
    assert payloads
    assert payloads[0].get("install_off") is True
    assert payloads[0].get("installed") is None
    assert payloads[0].get("active_package") is None
    assert payloads[0].get("router_sync") is None
    assert payloads[0].get("scipkg_root") is None


def test_recompile_runs_three_passes_then_installs(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {"packages": {}, "active_package": "newpkg"},
    )
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {"updated": True})

    pass_calls: list[dict[str, object]] = []

    def fake_pass(
        _project_root: Path,
        *,
        prompt: str,
        pass_index: int,
        total_passes: int,
        provider: str,
        provider_bin: str,
    ) -> dict[str, object]:
        pass_calls.append(
            {
                "pass": pass_index,
                "prompt": prompt,
                "total": total_passes,
                "provider": provider,
                "provider_bin": provider_bin,
            }
        )
        assistant_text = (
            "<compile_profile>{\"package_name\":\"newpkg\"}</compile_profile>"
            if pass_index == 1
            else ""
        )
        return {
            "pass": pass_index,
            "status": "ok",
            "return_code": 0,
            "assistant_text": assistant_text,
        }

    monkeypatch.setattr(cli, "_run_codex_compile_pass", fake_pass)
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {
            "evidence_dir": "skills/.evidence",
            "coverage_report": "skills/.evidence/recompile_coverage.md",
            "source_inventory": {
                "total_source_files": 120,
                "referenced_source_files": 95,
                "uncovered_source_files": 25,
            },
        },
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 31,
        },
    )
    monkeypatch.setattr(
        cli,
        "_write_compile_report",
        lambda *_a, **_k: "skills/.compile_report.json",
    )

    install_calls: list[dict[str, object]] = []

    def fake_install(
        root: Path,
        package_id: str,
        *,
        local_path: Path,
        title: str | None = None,
        activate: bool = False,
        force: bool = False,
    ) -> dict[str, object]:
        install_calls.append(
            {
                "root": root,
                "package_id": package_id,
                "local_path": local_path,
                "title": title,
                "activate": activate,
                "force": force,
            }
        )
        return {"id": package_id}

    monkeypatch.setattr(cli, "install_from_local_path", fake_install)

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--json",
            "--core-skill-count",
            "5",
        ]
    )
    assert code == 0
    assert len(pass_calls) == 3
    assert all("recompile" in str(item["prompt"]).lower() for item in pass_calls)

    assert len(install_calls) == 1
    assert install_calls[0]["root"] == scipkg_root
    assert install_calls[0]["package_id"] == "newpkg"
    assert install_calls[0]["local_path"] == project_root
    assert install_calls[0]["force"] is True

    assert payloads
    assert payloads[0].get("recompiled_package_id") == "newpkg"
    assert payloads[0].get("validation", {}).get("source_links_total") == 31
    assert payloads[0].get("validation_enforced") is False
    assert not (project_root / "sci-skills-generator").exists()


def test_recompile_validation_non_blocking_by_default(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": 1,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence"},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": False,
            "errors": ["skills/newpkg-api/references/source_map.md missing source links"],
            "warnings": [],
            "source_links_total": 0,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **_k: {"id": "newpkg"},
    )

    code = cli.main(["recompile", "newpkg", str(project_root)])
    assert code == 0
    out = capsys.readouterr().out
    assert "non-blocking" in out
    assert not (project_root / "sci-skills-generator").exists()


def test_recompile_strict_validation_blocks_install(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": 1,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence"},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": False,
            "errors": ["skills/newpkg-api/references/source_map.md missing source links"],
            "warnings": [],
            "source_links_total": 0,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("install should not run")
        ),
    )

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--strict-compile-validation",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Recompile validation failed" in err
    assert not (project_root / "sci-skills-generator").exists()


def test_recompile_existing_package_id_updates_by_default(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {
            "packages": {"newpkg": {"id": "newpkg"}},
            "active_package": "newpkg",
        },
    )
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {"updated": True})
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": 1,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence"},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {"ok": True, "errors": [], "warnings": [], "source_links_total": 10},
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )

    install_forces: list[bool] = []
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **kwargs: install_forces.append(bool(kwargs.get("force", False)))
        or {"id": "newpkg"},
    )

    code = cli.main(["recompile", "newpkg", str(project_root)])
    assert code == 0
    assert install_forces == [True]


def test_recompile_force_flag_is_accepted_as_compat_noop(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {
            "packages": {"newpkg": {"id": "newpkg"}},
            "active_package": "newpkg",
        },
    )
    monkeypatch.setattr(cli, "sync_router_rules", lambda _root: {"updated": True})
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": 1,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        },
    )
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_build_recompile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence"},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 10,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )
    install_forces: list[bool] = []
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **kwargs: install_forces.append(bool(kwargs.get("force", False)))
        or {"id": "newpkg"},
    )

    code = cli.main(["recompile", "newpkg", str(project_root), "--force"])
    assert code == 0
    assert install_forces == [True]
