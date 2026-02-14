from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink import cli


def _make_tool_source(path: Path) -> None:
    (path / "scripts").mkdir(parents=True, exist_ok=True)
    (path / "references").mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text("skill", encoding="utf-8")
    (path / "scripts" / "generate_skills_folder.py").write_text(
        "print('ok')\n", encoding="utf-8"
    )


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


def test_compile_runs_three_passes_then_installs(monkeypatch, tmp_path: Path) -> None:
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

    call_state = {"calls": 0, "tool_exists": []}

    def fake_subprocess_run(cmd, check=False):
        _ = check
        call_state["calls"] += 1
        call_state["tool_exists"].append(
            (project_root / "sci-skills-generator").exists()
        )
        return SimpleNamespace(returncode=0, cmd=cmd)

    monkeypatch.setattr(cli.subprocess, "run", fake_subprocess_run)

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

    code = cli.main(["compile", "newpkg", str(project_root), "--json"])
    assert code == 0
    assert call_state["calls"] == 3
    assert call_state["tool_exists"] == [True, True, False]
    assert not (project_root / "sci-skills-generator").exists()

    assert len(install_calls) == 1
    assert install_calls[0]["root"] == scipkg_root
    assert install_calls[0]["package_id"] == "newpkg"
    assert install_calls[0]["local_path"] == project_root
    assert install_calls[0]["force"] is False

    assert payloads
    compile_runs = payloads[0].get("compile_runs")
    assert isinstance(compile_runs, list)
    assert len(compile_runs) == 3
    assert all(item.get("status") == "ok" for item in compile_runs)


def test_compile_cleans_up_tool_on_pass_failure(
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

    call_count = {"value": 0}

    def fake_subprocess_run(_cmd, check=False):
        _ = check
        call_count["value"] += 1
        returncode = 1 if call_count["value"] == 2 else 0
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(cli.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("install should not run")
        ),
    )

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 2
    assert call_count["value"] == 2
    assert not (project_root / "sci-skills-generator").exists()
    err = capsys.readouterr().err
    assert "compile pass 2/3" in err


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

    build_calls: list[dict[str, object]] = []

    def fake_build_exec_command(
        *,
        provider: str,
        provider_bin: str,
        repo_dir: Path,
        prompt: str,
        sandbox_policy: str,
        sandbox_mode: str | None,
        json_output: bool,
    ) -> list[str]:
        build_calls.append(
            {
                "provider": provider,
                "provider_bin": provider_bin,
                "repo_dir": repo_dir,
                "prompt": prompt,
                "sandbox_policy": sandbox_policy,
                "sandbox_mode": sandbox_mode,
                "json_output": json_output,
            }
        )
        return [provider_bin, "exec", prompt]

    monkeypatch.setattr(cli, "build_exec_command", fake_build_exec_command)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda _cmd, check=False: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(
        cli,
        "install_from_local_path",
        lambda *_a, **_k: {"id": "newpkg"},
    )

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 0
    assert len(build_calls) == 3
    assert all(call["provider"] == "gemini" for call in build_calls)
    assert all(call["provider_bin"] == "gemini-bin" for call in build_calls)
    assert all(call["sandbox_policy"] == "enforce" for call in build_calls)
    assert all(
        call["sandbox_mode"] == cli.DEFAULT_COMPILE_SANDBOX for call in build_calls
    )
    assert all(call["json_output"] is False for call in build_calls)


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
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("subprocess should not run")
        ),
    )

    code = cli.main(["compile", "newpkg", str(project_root)])
    assert code == 2
    err = capsys.readouterr().err
    assert "not implemented yet" in err
    assert "fermilink agent codex" in err
