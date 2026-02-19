from __future__ import annotations

import json
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


def _make_minimal_paper_tutorial(
    project_root: Path,
    *,
    skill_id: str,
    figure_id: str = "fig_001",
    root_skill_text: str | None = None,
) -> None:
    skill_root = project_root / "skills" / skill_id
    (skill_root / "references").mkdir(parents=True, exist_ok=True)
    (skill_root / "assets").mkdir(parents=True, exist_ok=True)
    (skill_root / "playbooks").mkdir(parents=True, exist_ok=True)
    default_root = "\n".join(
        [
            f"# {skill_id}",
            "",
            "## Core Simulation Strategy",
            "- baseline protocol",
            "",
            "## Minimal Execution Recipes",
            "- stage runs under `projects/YYYY-MM-DD-demo/` and copy inputs from `assets/`",
            "- `cd projects/YYYY-MM-DD-demo && python playbooks/fig_001.py`",
            "",
            "## Figure Routing",
            (
                f"- `{figure_id}`: cavity-water response baseline scope with fixed "
                f"thermostat/integrator settings; playbook `playbooks/{figure_id}.md`"
            ),
            "",
            "## Beyond Manuscript Exploration",
            "- explore nearby parameter regimes with validation checks",
        ]
    )
    (skill_root / "SKILL.md").write_text(
        root_skill_text if root_skill_text is not None else default_root,
        encoding="utf-8",
    )
    (skill_root / "references" / "doc_map.md").write_text("doc map", encoding="utf-8")
    (skill_root / "references" / "source_map.md").write_text(
        "source map", encoding="utf-8"
    )
    (skill_root / "playbooks" / f"{figure_id}.md").write_text(
        "playbook", encoding="utf-8"
    )
    (project_root / "skills" / "newpkg-index" / "SKILL.md").write_text(
        f"advanced route to {skill_id}",
        encoding="utf-8",
    )
    figure_map_path = project_root / cli.RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH
    figure_map_path.parent.mkdir(parents=True, exist_ok=True)
    figure_map_path.write_text(
        json.dumps(
            {
                "version": 1,
                "figures": [{"id": figure_id, "files": [], "unknowns": []}],
                "global_unknowns": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def test_derive_recompile_paper_skill_id_prefers_scope_summary(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "skills").mkdir(parents=True, exist_ok=True)
    doc_path = project_root / "manuscript_revised.md"
    doc_path.write_text("# manuscript\n", encoding="utf-8")

    skill_id = cli._derive_recompile_paper_skill_id(
        project_root,
        package_id="newpkg",
        doc_path=doc_path,
        paper_plan={
            "global_assumptions": [
                "cavity water single mode infrared response study",
            ],
            "figures": [
                {
                    "id": "fig_001",
                    "title": "cavity water detuning sweep",
                    "objective": "track lower and upper polariton branch shifts",
                }
            ],
        },
    )
    assert skill_id.startswith("paper_tutorial_")
    assert "manuscript" not in skill_id
    assert "revised" not in skill_id
    assert "cavity" in skill_id
    assert "water" in skill_id


def test_derive_recompile_paper_skill_id_avoids_generic_doc_stem(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "skills").mkdir(parents=True, exist_ok=True)
    doc_path = project_root / "manuscript_revised.md"
    doc_path.write_text("# manuscript\n", encoding="utf-8")

    skill_id = cli._derive_recompile_paper_skill_id(
        project_root,
        package_id="newpkg",
        doc_path=doc_path,
    )
    assert skill_id == "paper_tutorial_newpkg"


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
    assert str(pass_calls[0]["prompt"]).startswith(cli.RECOMPILE_PROMPT_1)
    assert str(pass_calls[1]["prompt"]).startswith(cli.RECOMPILE_PROMPT_2)
    assert str(pass_calls[2]["prompt"]).startswith(cli.RECOMPILE_PROMPT_3)
    assert "Compile memory file:" in str(pass_calls[0]["prompt"])
    assert "Skill plan JSON file:" in str(pass_calls[1]["prompt"])

    assert len(install_calls) == 1
    assert install_calls[0]["root"] == scipkg_root
    assert install_calls[0]["package_id"] == "newpkg"
    assert install_calls[0]["local_path"] == project_root
    assert install_calls[0]["force"] is True

    assert payloads
    assert payloads[0].get("recompiled_package_id") == "newpkg"
    assert payloads[0].get("validation", {}).get("source_links_total") == 31
    assert payloads[0].get("validation_enforced") is False
    memory_rel = str(payloads[0].get("compile_memory") or "")
    assert memory_rel == cli.COMPILE_MEMORY_REL_PATH
    assert (project_root / memory_rel).is_file()
    skill_plan_rel = str(payloads[0].get("skill_plan_path") or "")
    assert skill_plan_rel == cli.COMPILE_SKILL_PLAN_REL_PATH
    assert (project_root / skill_plan_rel).is_file()
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


def test_recompile_comment_requires_doc(tmp_path: Path, capsys) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    code = cli.main(
        ["recompile", "newpkg", str(project_root), "--comment", "focus figure 2"]
    )
    assert code == 2
    assert "--comment requires --doc" in capsys.readouterr().err


def test_recompile_rejects_missing_doc_path(tmp_path: Path, capsys) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    missing_doc = project_root / "missing.tex"
    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--doc",
            str(missing_doc),
        ]
    )
    assert code == 2
    assert "--doc does not exist" in capsys.readouterr().err


def test_recompile_rejects_non_directory_data_dir(tmp_path: Path, capsys) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    doc_path = project_root / "paper.md"
    doc_path.write_text("# paper\n", encoding="utf-8")
    not_a_dir = project_root / "data.txt"
    not_a_dir.write_text("x", encoding="utf-8")

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--doc",
            str(doc_path),
            "--data-dir",
            str(not_a_dir),
        ]
    )
    assert code == 2
    assert "--data-dir must be a directory" in capsys.readouterr().err


def test_recompile_data_dir_requires_doc(tmp_path: Path, capsys) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    data_dir = project_root / "supplementary"
    data_dir.mkdir(parents=True, exist_ok=True)

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--data-dir",
            str(data_dir),
        ]
    )
    assert code == 2
    assert "--data-dir requires --doc" in capsys.readouterr().err


def test_recompile_memory_rejects_doc_combo(tmp_path: Path, capsys) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    memory_path = project_root / "memory.md"
    memory_path.write_text("# memory\n", encoding="utf-8")
    doc_path = project_root / "paper.md"
    doc_path.write_text("# paper\n", encoding="utf-8")

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--memory",
            str(memory_path),
            "--doc",
            str(doc_path),
        ]
    )
    assert code == 2
    assert "--memory cannot be combined" in capsys.readouterr().err


def test_recompile_memory_mode_builds_plan_from_recursive_memory(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    (project_root / "skills" / "newpkg-core").mkdir(parents=True, exist_ok=True)
    (project_root / "skills" / "newpkg-core" / "SKILL.md").write_text(
        "# core\n", encoding="utf-8"
    )
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)

    memory_root = tmp_path / "memories"
    (memory_root / "projects").mkdir(parents=True, exist_ok=True)
    (memory_root / "projects" / "memory.md").write_text(
        "\n".join(
            [
                "# Memory A",
                "",
                "### Suggested skills updates",
                "- (newpkg | environment import failures under conda env | add explicit conda run troubleshooting note | failed import in mxl env | proposed)",
                "- (otherpkg | unrelated | ignore | evidence | proposed)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (memory_root / "nested" / "deep").mkdir(parents=True, exist_ok=True)
    (memory_root / "nested" / "deep" / "memory.md").write_text(
        "\n".join(
            [
                "# Memory B",
                "",
                "### Suggested skills updates",
                "- (newpkg | parameter defaults miss stable convergence window | add playbook note for extra tuning parameter | figure-2 failed unless damping adjusted | proposed)",
                "- (newpkg | old resolved issue | obsolete update | old evidence | done)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(
        cli,
        "resolve_scipkg_root",
        lambda: (_ for _ in ()).throw(AssertionError("should not resolve scipkg root")),
    )

    pass_calls: list[dict[str, object]] = []

    def fake_pass(*_a, **kwargs):
        pass_calls.append(kwargs)
        return {
            "pass": int(kwargs.get("pass_index") or 1),
            "status": "ok",
            "return_code": 0,
            "assistant_text": (
                "<memory_update_plan>"
                "{\"version\":1,\"summary\":\"memory refresh\",\"operations\":["
                "{\"change_type\":\"append\",\"classification\":\"machine_specific\","
                "\"target_skill_id\":\"newpkg-core\","
                "\"target_path\":\"skills/newpkg-core/SKILL.md\","
                "\"issue_pattern\":\"environment import failures under conda env\","
                "\"proposed_skill_update\":\"add explicit conda run troubleshooting note\","
                "\"proposed_append_markdown\":\"### note\\n- conda run hint\","
                "\"evidence\":\"failed import in mxl env\",\"status\":\"proposed\"},"
                "{\"change_type\":\"append\",\"classification\":\"package_specific\","
                "\"target_skill_id\":\"newpkg-core\","
                "\"target_path\":\"skills/newpkg-core/SKILL.md\","
                "\"issue_pattern\":\"parameter defaults miss stable convergence window\","
                "\"proposed_skill_update\":\"add playbook note for extra tuning parameter\","
                "\"proposed_append_markdown\":\"### tuning\\n- add damping\","
                "\"evidence\":\"figure-2 failed unless damping adjusted\",\"status\":\"proposed\"}"
                "]}"
                "</memory_update_plan>"
            ),
        }

    monkeypatch.setattr(cli, "_run_codex_compile_pass", fake_pass)

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--memory",
            str(memory_root),
            "--json",
        ]
    )
    assert code == 0
    assert len(pass_calls) == 1
    assert int(pass_calls[0].get("total_passes") or 0) == 1
    prompt = str(pass_calls[0].get("prompt") or "")
    assert prompt.startswith(cli.RECOMPILE_MEMORY_PROMPT_1_PLAN)
    assert "Filtered suggested updates payload (2 entries" in prompt

    assert payloads
    payload = payloads[0]
    assert payload.get("memory_mode") is True
    assert payload.get("install_off") is True
    assert payload.get("installed") is None
    assert payload.get("active_package") is None
    assert payload.get("router_sync") is None
    assert payload.get("scipkg_root") is None
    memory_apply = payload.get("memory_apply")
    assert isinstance(memory_apply, dict)
    assert int(memory_apply.get("applied_count") or 0) == 2
    modified_files = memory_apply.get("modified_files")
    assert isinstance(modified_files, list)
    assert "skills/newpkg-core/SKILL.md" in modified_files
    assert "skills/user-specific-settings/SKILL.md" in modified_files

    suggestions_payload = payload.get("memory_suggestions")
    assert isinstance(suggestions_payload, dict)
    suggestions = suggestions_payload.get("suggestions")
    assert isinstance(suggestions, list)
    assert len(suggestions) == 2
    assert int(suggestions_payload.get("skipped_closed_entries") or 0) == 1
    assert len(suggestions_payload.get("memory_sources") or []) == 2

    memory_plan_path = str(payload.get("memory_plan_path") or "")
    assert memory_plan_path == cli.RECOMPILE_MEMORY_PLAN_REL_PATH
    plan_file = project_root / memory_plan_path
    assert plan_file.is_file()
    plan_payload = json.loads(plan_file.read_text(encoding="utf-8"))
    operations = plan_payload.get("operations")
    assert isinstance(operations, list)
    assert len(operations) == 2
    machine_targets = [
        item.get("target_path")
        for item in operations
        if item.get("classification") == "machine_specific"
    ]
    assert machine_targets == ["skills/user-specific-settings/SKILL.md"]
    assert all(item.get("status") == "accepted" for item in operations)
    user_settings_skill = (
        project_root / "skills" / "user-specific-settings" / "SKILL.md"
    )
    assert user_settings_skill.is_file()
    user_settings_text = user_settings_skill.read_text(encoding="utf-8")
    assert "### note" in user_settings_text
    core_skill_text = (
        project_root / "skills" / "newpkg-core" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "### tuning" in core_skill_text
    assert not (project_root / "sci-skills-generator").exists()


def test_recompile_memory_directory_requires_memory_files(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    memory_root = tmp_path / "memories"
    memory_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)

    code = cli.main(
        [
            "recompile",
            "newpkg",
            str(project_root),
            "--memory",
            str(memory_root),
        ]
    )
    assert code == 2
    assert "No memory.md files found" in capsys.readouterr().err


def test_recompile_accepts_doc_data_dir_and_comment(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    doc_path = project_root / "paper.md"
    doc_path.write_text("# paper\n", encoding="utf-8")
    data_dir = project_root / "supplementary"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "input.json").write_text("{\"x\": 1}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    seen_prompts: list[str] = []

    def fake_pass(*_a, **_k):
        pass_index = int(_k.get("pass_index") or 1)
        seen_prompts.append(str(_k.get("prompt") or ""))
        assistant_text = ""
        if pass_index == 1:
            assistant_text = (
                "<compile_profile>{\"package_name\":\"newpkg\"}</compile_profile>"
                "<paper_plan>"
                "{\"version\":1,\"paper_source\":\"paper.md\",\"used_packages\":[\"newpkg\"],"
                "\"figures\":[{\"id\":\"fig_001\",\"title\":\"Figure 1\",\"targets\":[\"Figure 1\"],"
                "\"objective\":\"obj\",\"simulation_config\":[\"cfg\"],"
                "\"parameter_requirements\":[\"p\"],\"required_packages\":[\"newpkg\"],"
                "\"expected_artifacts\":[\"plot\"],\"acceptance_checks\":[\"check\"]}]}"
                "</paper_plan>"
            )
        return {
            "pass": pass_index,
            "status": "ok",
            "return_code": 0,
            "assistant_text": assistant_text,
        }

    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        fake_pass,
    )
    monkeypatch.setattr(cli, "_load_compile_profile", lambda *_a, **_k: _default_profile())
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
            "source_links_total": 7,
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
            "--doc",
            str(doc_path),
            "--data-dir",
            str(data_dir),
            "--comment",
            "focus on figure 2",
        ]
    )
    assert code == 0
    assert payloads
    payload = payloads[0]
    assert payload.get("doc_path") == str(doc_path.resolve())
    assert payload.get("data_dir") == str(data_dir.resolve())
    assert payload.get("comment") == "focus on figure 2"
    assert payload.get("paper_mode") is True
    paper_data_context = payload.get("paper_data_context")
    assert isinstance(paper_data_context, dict)
    assert paper_data_context.get("enabled") is True
    artifacts = paper_data_context.get("artifacts")
    assert isinstance(artifacts, dict)
    manifest_rel = str(artifacts.get("manifest") or "")
    manifest_full_rel = str(artifacts.get("manifest_full") or "")
    summary_rel = str(artifacts.get("summary") or "")
    assert manifest_rel
    assert manifest_full_rel
    assert summary_rel
    assert (project_root / manifest_rel).is_file()
    assert (project_root / manifest_full_rel).is_file()
    assert (project_root / summary_rel).is_file()
    assert seen_prompts
    assert seen_prompts[0].startswith(cli.RECOMPILE_PAPER_PROMPT_1_PLAN)
    assert "Original manuscript content" in seen_prompts[0]
    assert seen_prompts[1].startswith(cli.RECOMPILE_PAPER_PROMPT_2_TUTORIAL)
    assert "# paper" not in seen_prompts[1]
    assert seen_prompts[2].startswith(cli.RECOMPILE_PAPER_PROMPT_3_AUDIT)
    paper_context = payload.get("paper_context")
    assert isinstance(paper_context, dict)
    context_rel = str(paper_context.get("context_path") or "")
    assert context_rel
    assert (project_root / context_rel).is_file()
    paper_plan = payload.get("paper_plan")
    assert isinstance(paper_plan, dict)
    assert paper_plan.get("figures")
    assert payload.get("paper_plan_path") == cli.RECOMPILE_PAPER_PLAN_REL_PATH
    assert isinstance(payload.get("paper_skill_id"), str)
    paper_staged_assets = payload.get("paper_staged_assets")
    assert isinstance(paper_staged_assets, dict)
    assert int(paper_staged_assets.get("staged_count") or 0) >= 1
    staged_manifest_rel = str(paper_staged_assets.get("manifest_path") or "")
    assert staged_manifest_rel
    assert (project_root / staged_manifest_rel).is_file()
    staged_files = paper_staged_assets.get("staged_files")
    assert isinstance(staged_files, list)
    assert staged_files
    staged_path = str(staged_files[0].get("staged_path") or "")
    assert staged_path
    assert (project_root / staged_path).is_file()


def test_recompile_doc_only_still_writes_disabled_staged_assets_manifest(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    doc_path = project_root / "paper.md"
    doc_path.write_text("# paper\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)

    def fake_pass(*_a, **_k):
        pass_index = int(_k.get("pass_index") or 1)
        assistant_text = ""
        if pass_index == 1:
            assistant_text = (
                "<compile_profile>{\"package_name\":\"newpkg\"}</compile_profile>"
                "<paper_plan>"
                "{\"version\":1,\"paper_source\":\"paper.md\",\"used_packages\":[\"newpkg\"],"
                "\"figures\":[{\"id\":\"fig_001\",\"title\":\"Figure 1\",\"targets\":[\"Figure 1\"],"
                "\"objective\":\"obj\",\"simulation_config\":[\"cfg\"],"
                "\"parameter_requirements\":[\"p\"],\"required_packages\":[\"newpkg\"],"
                "\"expected_artifacts\":[\"plot\"],\"acceptance_checks\":[\"check\"]}]}"
                "</paper_plan>"
            )
        return {
            "pass": pass_index,
            "status": "ok",
            "return_code": 0,
            "assistant_text": assistant_text,
        }

    monkeypatch.setattr(cli, "_run_codex_compile_pass", fake_pass)
    monkeypatch.setattr(cli, "_load_compile_profile", lambda *_a, **_k: _default_profile())
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
            "source_links_total": 7,
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
            "--doc",
            str(doc_path),
        ]
    )
    assert code == 0
    assert payloads
    payload = payloads[0]
    paper_staged_assets = payload.get("paper_staged_assets")
    assert isinstance(paper_staged_assets, dict)
    assert paper_staged_assets.get("enabled") is False
    manifest_rel = str(paper_staged_assets.get("manifest_path") or "")
    assert manifest_rel
    assert (project_root / manifest_rel).is_file()


def test_validate_recompile_paper_outputs_flags_missing_paper_skill(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    doc_path = project_root / "paper.md"
    doc_path.write_text("# manuscript\n", encoding="utf-8")

    result = cli._validate_recompile_paper_outputs(
        project_root,
        doc_path=doc_path,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("Missing paper plan payload" in str(item) for item in errors)


def test_validate_recompile_paper_outputs_rejects_evidence_path_and_escape(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(project_root, skill_id=skill_id)
    (project_root / "skills" / skill_id / "SKILL.md").write_text(
        "\n".join(
            [
                f"# {skill_id}",
                "",
                "## Core Simulation Strategy",
                "- use staged inputs from `skills/.evidence/paper_context/staged_assets`",
                "- run helper at `../cavmd_examples_h2o/water_VUSC/collect.py`",
                "",
                "## Minimal Execution Recipes",
                "- `python ../cavmd_examples_h2o/water_VUSC/collect.py`",
                "",
                "## Figure Routing",
                "- `fig_001` -> `playbooks/fig_001.md`",
                "",
                "## Beyond Manuscript Exploration",
                "- sweep coupling strengths around baseline",
            ]
        ),
        encoding="utf-8",
    )
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("skills/.evidence" in str(item) for item in errors)
    assert any("path escapes tutorial skill root" in str(item) for item in errors)


def test_validate_recompile_paper_outputs_rejects_workspace_runtime_path(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(
        project_root,
        skill_id=skill_id,
        root_skill_text="\n".join(
            [
                f"# {skill_id}",
                "",
                "## Core Simulation Strategy",
                "- protocol",
                "",
                "## Minimal Execution Recipes",
                "- create `workspace/single_mode_g0/` and run there",
                "",
                "## Figure Routing",
                "- `fig_001` -> `playbooks/fig_001.md`",
                "",
                "## Beyond Manuscript Exploration",
                "- explore",
            ]
        ),
    )
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("workspace/" in str(item) for item in errors)
    assert any("projects/yyyy-mm-dd-<scope>" in str(item).lower() for item in errors)


def test_validate_recompile_paper_outputs_requires_projects_runtime_instruction(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(
        project_root,
        skill_id=skill_id,
        root_skill_text="\n".join(
            [
                f"# {skill_id}",
                "",
                "## Core Simulation Strategy",
                "- protocol",
                "",
                "## Minimal Execution Recipes",
                "- run commands for figure reproduction",
                "",
                "## Figure Routing",
                "- `fig_001` -> `playbooks/fig_001.md`",
                "",
                "## Beyond Manuscript Exploration",
                "- explore",
            ]
        ),
    )
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("projects/yyyy-mm-dd-<scope>" in str(item).lower() for item in errors)


def test_validate_recompile_paper_outputs_requires_figure_scope_summary(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(
        project_root,
        skill_id=skill_id,
        root_skill_text="\n".join(
            [
                f"# {skill_id}",
                "",
                "## Core Simulation Strategy",
                "- protocol",
                "",
                "## Minimal Execution Recipes",
                "- stage runs under `projects/YYYY-MM-DD-demo/` and copy inputs from `assets/`",
                "",
                "## Figure Routing",
                "- `fig_001` (Figure 1a-1e): `playbooks/fig_001.md`",
                "",
                "## Beyond Manuscript Exploration",
                "- explore",
            ]
        ),
    )
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("brief scope description" in str(item).lower() for item in errors)


def test_validate_recompile_paper_outputs_accepts_scope_summarized_routing(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(project_root, skill_id=skill_id)
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
        staged_assets={"staged_count": 0},
    )
    assert result["ok"] is True


def test_validate_recompile_paper_outputs_requires_root_sections(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(
        project_root,
        skill_id=skill_id,
        root_skill_text="\n".join(
            [
                f"# {skill_id}",
                "",
                "## Core Simulation Strategy",
                "- protocol",
                "",
                "## Minimal Execution Recipes",
                "- `python playbooks/fig_001.py`",
                "",
                "## Figure Routing",
                "- `fig_001` -> `playbooks/fig_001.md`",
            ]
        ),
    )
    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("## beyond manuscript exploration" in str(item).lower() for item in errors)


def test_validate_recompile_paper_outputs_rejects_oversized_assets(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    skill_id = "paper_tutorial_demo"
    _make_minimal_paper_tutorial(project_root, skill_id=skill_id)
    oversized = project_root / "skills" / skill_id / "assets" / "trajectory.xyz"
    oversized.parent.mkdir(parents=True, exist_ok=True)
    with oversized.open("wb") as handle:
        handle.truncate((25 * 1024 * 1024) + 1)

    result = cli._validate_recompile_paper_outputs(
        project_root,
        paper_plan={"version": 1, "figures": [{"id": "fig_001"}]},
        paper_skill_id=skill_id,
        staged_assets={"staged_count": 0},
    )
    assert result["ok"] is False
    errors = result.get("errors")
    assert isinstance(errors, list)
    assert any("oversized files" in str(item) for item in errors)


def test_recompile_strict_validation_blocks_on_paper_validation(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    _make_existing_skills(project_root)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"
    doc_path = project_root / "paper.md"
    doc_path.write_text("# manuscript\n", encoding="utf-8")
    data_dir = project_root / "supplementary"
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "input.json").write_text("{\"x\": 1}\n", encoding="utf-8")

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})
    monkeypatch.setattr(
        cli,
        "_run_codex_compile_pass",
        lambda *_a, **_k: {
            "pass": int(_k.get("pass_index") or 1),
            "status": "ok",
            "return_code": 0,
            "assistant_text": (
                "<compile_profile>{\"package_name\":\"newpkg\"}</compile_profile>"
                "<paper_plan>"
                "{\"version\":1,\"paper_source\":\"paper.md\",\"used_packages\":[\"newpkg\"],"
                "\"figures\":[{\"id\":\"fig_001\",\"title\":\"Figure 1\",\"targets\":[\"Figure 1\"],"
                "\"objective\":\"obj\",\"simulation_config\":[\"cfg\"],"
                "\"parameter_requirements\":[\"p\"],\"required_packages\":[\"newpkg\"],"
                "\"expected_artifacts\":[\"plot\"],\"acceptance_checks\":[\"check\"]}]}"
                "</paper_plan>"
                if int(_k.get("pass_index") or 1) == 1
                else ""
            ),
        },
    )
    monkeypatch.setattr(cli, "_load_compile_profile", lambda *_a, **_k: _default_profile())
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
            "source_links_total": 5,
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
            "--doc",
            str(doc_path),
            "--data-dir",
            str(data_dir),
        ]
    )
    assert code == 2
    assert "Recompile validation failed" in capsys.readouterr().err
