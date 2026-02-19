from __future__ import annotations

from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy


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


@pytest.fixture(autouse=True)
def _stub_compile_repo_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_ensure_compile_repo_ready", lambda _path: False)


def test_compile_rejects_existing_package_id(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "load_registry",
        lambda _root: {"packages": {"existingpkg": {"id": "existingpkg"}}},
    )

    code = cli.main(["compile", "existingpkg", str(project_root)])
    assert code == 2
    err = capsys.readouterr().err
    assert "already exists" in err


def test_compile_install_off_skips_registry_and_install(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 9,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "compile",
            "existingpkg",
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


def test_compile_auto_initializes_git_repo_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)

    init_calls: list[Path] = []
    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(
        cli,
        "_ensure_compile_repo_ready",
        lambda repo_dir: init_calls.append(repo_dir) or True,
    )
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
    monkeypatch.setattr(cli, "_load_compile_profile", lambda *_a, **_k: _default_profile())
    monkeypatch.setattr(
        cli,
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 3,
        },
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )

    payloads: list[dict[str, object]] = []
    monkeypatch.setattr(cli, "_print_json", lambda payload: payloads.append(payload))

    code = cli.main(
        [
            "compile",
            "newpkg",
            str(project_root),
            "--install-off",
            "--json",
        ]
    )
    assert code == 0
    assert init_calls == [project_root]
    assert payloads and payloads[0].get("git_repo_initialized") is True


def test_compile_runs_staged_pipeline_then_installs(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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

    profile_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda project_root, default_package_name, assistant_text: profile_calls.append(
            {
                "project_root": project_root,
                "default_package_name": default_package_name,
                "assistant_text": assistant_text,
            }
        )
        or _default_profile(),
    )

    generation_calls: list[dict[str, object]] = []

    def fake_generation(
        project_root: Path,
        *,
        tool_dir: Path,
        profile: dict[str, object],
        max_skills: int,
        docs_only_override: bool = False,
    ) -> dict[str, object]:
        generation_calls.append(
            {
                "project_root": project_root,
                "tool_dir": tool_dir,
                "profile": dict(profile),
                "max_skills": max_skills,
                "docs_only_override": docs_only_override,
            }
        )
        return {
            "status": "ok",
            "return_code": 0,
            "docs_only": False,
            "max_skills": max_skills,
            "generator_script": "sci-skills-generator/scripts/generate_skills_folder.py",
        }

    monkeypatch.setattr(cli, "_run_compile_generator", fake_generation)

    evidence_calls: list[int] = []
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda _root, core_skill_count: evidence_calls.append(core_skill_count)
        or {"evidence_dir": "skills/.evidence", "core_skills": ["newpkg-api"]},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {
            "ok": True,
            "errors": [],
            "warnings": [],
            "source_links_total": 17,
            "core_skills_checked": ["newpkg-api"],
        },
    )
    monkeypatch.setattr(
        cli,
        "_write_compile_report",
        lambda _root, payload: "skills/.compile_report.json",
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
            "compile",
            "newpkg",
            str(project_root),
            "--json",
            "--max-skills",
            "25",
            "--core-skill-count",
            "4",
        ]
    )
    assert code == 0
    assert len(pass_calls) == 3
    assert [entry["pass"] for entry in pass_calls] == [1, 2, 3]
    assert profile_calls and "compile_profile" in profile_calls[0]["assistant_text"]
    assert len(generation_calls) == 1
    assert generation_calls[0]["max_skills"] == 25
    assert evidence_calls == [4]

    assert len(install_calls) == 1
    assert install_calls[0]["root"] == scipkg_root
    assert install_calls[0]["package_id"] == "newpkg"
    assert install_calls[0]["local_path"] == project_root
    assert install_calls[0]["force"] is False

    assert not (project_root / "sci-skills-generator").exists()
    assert payloads
    compile_runs = payloads[0].get("compile_runs")
    assert isinstance(compile_runs, list)
    assert len(compile_runs) == 3
    assert all(item.get("status") == "ok" for item in compile_runs)
    assert payloads[0].get("compile_report") == "skills/.compile_report.json"
    assert payloads[0].get("validation", {}).get("source_links_total") == 17
    assert payloads[0].get("validation_enforced") is False
    memory_rel = str(payloads[0].get("compile_memory") or "")
    assert memory_rel == cli.COMPILE_MEMORY_REL_PATH
    assert (project_root / memory_rel).is_file()
    skill_plan_rel = str(payloads[0].get("skill_plan_path") or "")
    assert skill_plan_rel == cli.COMPILE_SKILL_PLAN_REL_PATH
    assert (project_root / skill_plan_rel).is_file()


def test_compile_keep_compile_artifacts_retains_tool_dir(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {"ok": True, "errors": [], "warnings": [], "source_links_total": 0},
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )
    monkeypatch.setattr(
        cli, "install_from_local_path", lambda *_a, **_k: {"id": "newpkg"}
    )

    code = cli.main(
        ["compile", "newpkg", str(project_root), "--keep-compile-artifacts"]
    )
    assert code == 0
    assert (project_root / "sci-skills-generator").is_dir()


def test_compile_validation_findings_are_non_blocking_by_default(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
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

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 0
    out = capsys.readouterr().out
    assert "non-blocking" in out
    assert not (project_root / "sci-skills-generator").exists()


def test_compile_strict_validation_blocks_install(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
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
            "compile",
            "newpkg",
            str(project_root),
            "--strict-compile-validation",
        ]
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "Compile validation failed" in err
    assert not (project_root / "sci-skills-generator").exists()


def test_compile_inherits_provider_from_runtime_policy(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
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
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="gemini",
            sandbox_policy="bypass",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_provider_binary",
        lambda provider, codex_bin=None: f"{provider}-bin",
    )

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
                "provider": provider,
                "provider_bin": provider_bin,
                "prompt": prompt,
                "pass_index": pass_index,
                "total_passes": total_passes,
            }
        )
        return {
            "pass": pass_index,
            "status": "ok",
            "return_code": 0,
            "assistant_text": "",
        }

    monkeypatch.setattr(cli, "_run_codex_compile_pass", fake_pass)
    monkeypatch.setattr(
        cli,
        "_load_compile_profile",
        lambda *_a, **_k: _default_profile(),
    )
    monkeypatch.setattr(
        cli,
        "_run_compile_generator",
        lambda *_a, **_k: {"status": "ok", "return_code": 0},
    )
    monkeypatch.setattr(
        cli,
        "_build_compile_evidence_bundle",
        lambda *_a, **_k: {"evidence_dir": "skills/.evidence", "core_skills": []},
    )
    monkeypatch.setattr(
        cli,
        "_validate_compiled_skills",
        lambda *_a, **_k: {"ok": True, "errors": [], "warnings": [], "source_links_total": 5},
    )
    monkeypatch.setattr(
        cli, "_write_compile_report", lambda *_a, **_k: "skills/.compile_report.json"
    )
    monkeypatch.setattr(
        cli, "install_from_local_path", lambda *_a, **_k: {"id": "newpkg"}
    )

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 0
    assert len(pass_calls) == 3
    assert all(call["provider"] == "gemini" for call in pass_calls)
    assert all(call["provider_bin"] == "gemini-bin" for call in pass_calls)


def test_compile_errors_for_unimplemented_runtime_provider(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    tool_source = tmp_path / "tool-source"
    _make_tool_source(tool_source)
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_resolve_compile_tool_source", lambda: tool_source)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(cli, "load_registry", lambda _root: {"packages": {}})
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="gemini",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "resolve_provider_binary",
        lambda provider, codex_bin=None: f"{provider}-bin",
    )

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 2
    err = capsys.readouterr().err
    assert "not implemented yet" in err
    assert "fermilink agent codex" in err
    assert not (project_root / "sci-skills-generator").exists()


def test_validate_compiled_skills_flags_missing_source_links(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    skill_topic = project_root / "skills" / "mypkg-workflows"
    skill_index = project_root / "skills" / "mypkg-index"
    (skill_topic / "references").mkdir(parents=True, exist_ok=True)
    skill_index.mkdir(parents=True, exist_ok=True)

    (skill_index / "SKILL.md").write_text("index", encoding="utf-8")
    (skill_topic / "SKILL.md").write_text(
        """# Topic

## High-Signal Playbook
- Route: workflow
- Triage questions: q1
- Canonical workflow: step
- Minimal working example: run
- Pitfalls: check
- Convergence/validation checklist: criteria
""",
        encoding="utf-8",
    )
    (skill_topic / "references" / "doc_map.md").write_text(
        "Total docs grouped in this topic: 1\n",
        encoding="utf-8",
    )
    (skill_topic / "references" / "source_map.md").write_text(
        "- `src/missing_solver.py`\n",
        encoding="utf-8",
    )

    result = cli._validate_compiled_skills(
        project_root,
        profile={
            "docs_only": False,
            "source_dirs": ["src"],
        },
        core_skill_count=1,
    )
    assert result["ok"] is False
    assert any("no valid source-code entry links" in err for err in result["errors"])


def test_validate_compiled_skills_accepts_source_links_and_playbook(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    (project_root / "src").mkdir(parents=True, exist_ok=True)
    (project_root / "docs").mkdir(parents=True, exist_ok=True)
    (project_root / "src" / "solver.py").write_text(
        "def solve():\n    return 1\n", encoding="utf-8"
    )
    (project_root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

    skill_topic = project_root / "skills" / "mypkg-workflows"
    skill_index = project_root / "skills" / "mypkg-index"
    (skill_topic / "references").mkdir(parents=True, exist_ok=True)
    skill_index.mkdir(parents=True, exist_ok=True)
    (skill_index / "SKILL.md").write_text("index", encoding="utf-8")
    (skill_topic / "SKILL.md").write_text(
        """# Topic

## High-Signal Playbook
- Route: use this skill for workflow setup.
- Triage questions: what model, what boundary, what runtime?
- Canonical workflow: configure -> run -> inspect output.
- Minimal working example: python run.py --config config.yaml
- Pitfalls: unstable timestep, wrong units.
- Convergence/validation checklist: mesh, timestep, boundary, tolerances.
""",
        encoding="utf-8",
    )
    (skill_topic / "references" / "doc_map.md").write_text(
        "Total docs grouped in this topic: 1\n- `docs/guide.md`\n",
        encoding="utf-8",
    )
    (skill_topic / "references" / "source_map.md").write_text(
        "- `src/solver.py` | score: 10\n",
        encoding="utf-8",
    )

    result = cli._validate_compiled_skills(
        project_root,
        profile={
            "docs_only": False,
            "source_dirs": ["src"],
        },
        core_skill_count=1,
    )
    assert result["ok"] is True
    assert int(result["source_links_total"]) >= 1


def test_validate_compiled_skills_allows_relative_doc_map_reference_in_source_map(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    (project_root / "src").mkdir(parents=True, exist_ok=True)
    (project_root / "src" / "solver.py").write_text(
        "def solve():\n    return 1\n",
        encoding="utf-8",
    )

    skill_topic = project_root / "skills" / "mypkg-api"
    skill_index = project_root / "skills" / "mypkg-index"
    (skill_topic / "references").mkdir(parents=True, exist_ok=True)
    skill_index.mkdir(parents=True, exist_ok=True)

    (skill_index / "SKILL.md").write_text("index", encoding="utf-8")
    (skill_topic / "SKILL.md").write_text(
        """# API

## High-Signal Playbook
- Route: pick for solver API usage.
- Triage questions: which solver and tolerance?
- Canonical workflow: configure -> run -> inspect output.
- Minimal working example: python run.py --solver cg
- Pitfalls: unstable step size.
- Convergence/validation checklist: tolerances and residual trend.
""",
        encoding="utf-8",
    )
    (skill_topic / "references" / "doc_map.md").write_text(
        "- `docs/guide.md`\n",
        encoding="utf-8",
    )
    (skill_topic / "references" / "source_map.md").write_text(
        "- Related docs: `doc_map.md`\n"
        "- Entry: `src/solver.py`\n",
        encoding="utf-8",
    )

    result = cli._validate_compiled_skills(
        project_root,
        profile={
            "docs_only": False,
            "source_dirs": ["src"],
        },
        core_skill_count=1,
    )
    assert result["ok"] is True
    assert not any("doc_map.md" in err for err in result["errors"])


def test_load_compile_skill_plan_from_tagged_payload(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    assistant_text = (
        "<skill_plan>"
        "{\"version\":1,\"mode\":\"compile\",\"goal\":\"prioritize api\","
        "\"priority_skills\":[{\"skill_id\":\"mypkg api\",\"action\":\"refresh\","
        "\"reason\":\"api first\",\"must_cover\":[\"workflow\"],\"source_hints\":[\"src/api.py\"]}]}"
        "</skill_plan>"
    )

    plan = cli._load_compile_skill_plan(
        project_root,
        package_id="mypkg",
        mode="compile",
        assistant_text=assistant_text,
        available_skill_ids=["mypkg-api", "mypkg-index"],
        core_skill_ids=["mypkg-api"],
    )
    assert isinstance(plan, dict)
    priority = plan.get("priority_skills")
    assert isinstance(priority, list)
    assert priority
    assert priority[0].get("skill_id") == "mypkg-api"
    plan_path = project_root / cli.COMPILE_SKILL_PLAN_REL_PATH
    assert plan_path.is_file()


def test_compile_memory_lifecycle_records_run_summary(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)

    memory_rel = cli._ensure_compile_memory(
        project_root,
        package_id="mypkg",
        mode="compile",
    )
    assert memory_rel == cli.COMPILE_MEMORY_REL_PATH
    memory_path = project_root / memory_rel
    assert memory_path.is_file()
    skills_gitignore = project_root / "skills" / ".gitignore"
    assert skills_gitignore.is_file()
    gitignore_text = skills_gitignore.read_text(encoding="utf-8")
    assert ".evidence/" in gitignore_text

    cli._reset_compile_memory_short_term(
        project_root,
        package_id="mypkg",
        mode="compile",
        run_id="compile_20260219T010000Z",
        run_goal="test compile memory",
    )
    update = cli._record_compile_memory_run(
        project_root,
        package_id="mypkg",
        mode="compile",
        run_id="compile_20260219T010000Z",
        run_goal="test compile memory",
        skill_plan={
            "goal": "prioritize api coverage",
            "priority_skills": [{"skill_id": "mypkg-api", "action": "refresh"}],
        },
        pass_scope_diffs={
            "pass_2": {"changed": ["skills/mypkg-api/SKILL.md"]},
            "pass_3": {"changed": ["skills/mypkg-api/references/source_map.md"]},
        },
        evidence={
            "core_skills": ["mypkg-api"],
            "source_inventory": {
                "total_source_files": 10,
                "referenced_source_files": 7,
                "uncovered_source_files": 3,
            },
        },
        validation={"ok": True, "errors": [], "warnings": []},
        compile_report_path="skills/.compile_report.json",
    )
    assert isinstance(update, dict)
    assert update.get("memory_path") == cli.COMPILE_MEMORY_REL_PATH

    memory_text = memory_path.read_text(encoding="utf-8")
    assert "## Short-Term Memory (Current Run)" in memory_text
    assert "## Long-Term Memory (Persistent)" in memory_text
    assert "compile_20260219T010000Z" in memory_text
    assert "mypkg-api" in memory_text


def test_ensure_compile_memory_appends_evidence_ignore_to_existing_skills_gitignore(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    skills_root = project_root / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    gitignore_path = skills_root / ".gitignore"
    gitignore_path.write_text("*.tmp\n", encoding="utf-8")

    cli._ensure_compile_memory(
        project_root,
        package_id="mypkg",
        mode="recompile",
    )

    updated = gitignore_path.read_text(encoding="utf-8")
    assert "*.tmp" in updated
    assert ".evidence/" in updated


def test_build_compile_evidence_bundle_preserves_sidecar_files(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    skill_dir = project_root / "skills" / "mypkg-api"
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("# API\n", encoding="utf-8")

    evidence_root = project_root / "skills" / ".evidence"
    evidence_root.mkdir(parents=True, exist_ok=True)
    memory_path = evidence_root / "memory.md"
    memory_path.write_text("# memory\n", encoding="utf-8")
    skill_plan_path = evidence_root / "skill_plan.json"
    skill_plan_path.write_text("{\"version\":1}\n", encoding="utf-8")
    paper_context_path = evidence_root / "paper_context" / "paper_context.json"
    paper_context_path.parent.mkdir(parents=True, exist_ok=True)
    paper_context_path.write_text("{\"mode\":\"paper\"}\n", encoding="utf-8")
    stale_file = evidence_root / "old_topic.md"
    stale_file.write_text("stale\n", encoding="utf-8")

    manifest = cli._build_compile_evidence_bundle(project_root, core_skill_count=1)
    assert isinstance(manifest, dict)
    assert memory_path.is_file()
    assert skill_plan_path.is_file()
    assert paper_context_path.is_file()
    assert not stale_file.exists()
