from __future__ import annotations

import io
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli.commands import workflows as workflow_commands
from fermilink.runner import scientific_packages as scipkg


def test_exec_runs_with_routing_overlay_and_codex(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    calls: dict[str, object] = {}
    scipkg_root = tmp_path / "scientific_packages"

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: scipkg_root)
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
            model="gpt-5.3-codex",
            reasoning_effort="high",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "second_guess",
            "reason": "matched",
            "note": "second_guess_switch(maxwelllink->maxwelllink, conf=0.99)",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 5,
            "collision_count": 1,
            "linked_dependency_count": 2,
        },
    )

    def fake_run_exec(
        *,
        repo_dir: Path,
        prompt: str,
        sandbox: str,
        provider_bin_override: str,
        **_kwargs,
    ) -> int:
        calls["repo_dir"] = repo_dir
        calls["prompt"] = prompt
        calls["sandbox"] = sandbox
        calls["provider_bin_override"] = provider_bin_override
        calls["model"] = _kwargs.get("model")
        calls["reasoning_effort"] = _kwargs.get("reasoning_effort")
        return 0

    monkeypatch.setattr(cli, "_run_exec_provider_prompt", fake_run_exec)

    code = cli.main(["exec", "simulate", "a", "cavity", "--sandbox", "workspace-write"])
    assert code == 0
    assert calls["repo_dir"] == repo_dir
    assert "projects/memory.md" in str(calls["prompt"])
    assert "simulate a cavity" in str(calls["prompt"])
    assert calls["sandbox"] == "workspace-write"
    assert calls["model"] == "gpt-5.3-codex"
    assert calls["reasoning_effort"] == "high"
    memory_path = repo_dir / "projects" / "memory.md"
    assert memory_path.is_file()
    assert "simulate a cavity" in memory_path.read_text(encoding="utf-8")

    output = capsys.readouterr().out
    assert "[package] Using maxwelllink (selection: second_guess)" in output
    assert (
        "[overlay] linked entries: 5, linked dependencies: 2, collisions: 1" in output
    )


def test_exec_propagates_codex_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    monkeypatch.setattr(cli, "_run_exec_provider_prompt", lambda **_kwargs: 7)
    cleanup_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli,
        "_cleanup_exec_overlay_symlinks",
        lambda *, repo_dir, workspace_root: cleanup_calls.append(
            (repo_dir, workspace_root)
        ),
    )

    code = cli.main(["exec", "hello"])
    assert code == 7
    assert cleanup_calls == [(repo_dir, repo_dir)]


def test_exec_attempts_completion_checkpoint_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 0,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    monkeypatch.setattr(cli, "_run_exec_provider_prompt", lambda **_kwargs: 0)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    completion_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        workflow_commands,
        "_workflow_completion_commit",
        lambda *, repo_dir, mode_name: completion_calls.append(
            (Path(repo_dir), str(mode_name))
        )
        or {"status": "noop", "sha": "", "error": "", "memory_only": "false"},
    )

    code = cli.main(["exec", "hello"])
    assert code == 0
    assert completion_calls == [(repo_dir, "exec")]


def test_cleanup_exec_overlay_symlinks_removes_only_manifest_entries(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    managed_entry_source = tmp_path / "managed-entry"
    managed_entry_source.mkdir(parents=True, exist_ok=True)
    managed_dependency_source = tmp_path / "managed-dependency"
    managed_dependency_source.mkdir(parents=True, exist_ok=True)
    foreign_manifest_source = tmp_path / "foreign-manifest-source"
    foreign_manifest_source.mkdir(parents=True, exist_ok=True)
    foreign_actual_source = tmp_path / "foreign-actual-source"
    foreign_actual_source.mkdir(parents=True, exist_ok=True)
    user_source = tmp_path / "user-source"
    user_source.mkdir(parents=True, exist_ok=True)

    managed_entry_link = repo_dir / "skills"
    managed_entry_link.symlink_to(managed_entry_source, target_is_directory=True)
    managed_copy_entry = repo_dir / "public"
    managed_copy_entry.mkdir(parents=True, exist_ok=True)
    (managed_copy_entry / "index.html").write_text("copy", encoding="utf-8")
    foreign_link = repo_dir / "foreign-link"
    foreign_link.symlink_to(foreign_actual_source, target_is_directory=True)
    user_link = repo_dir / "user-link"
    user_link.symlink_to(user_source, target_is_directory=True)
    unmanaged_copy_entry = repo_dir / "user-dir"
    unmanaged_copy_entry.mkdir(parents=True, exist_ok=True)

    dependency_root = repo_dir / scipkg.PACKAGE_DEPENDENCIES_DIRNAME
    dependency_root.mkdir(parents=True, exist_ok=True)
    managed_dependency_link = dependency_root / "deppkg"
    managed_dependency_link.symlink_to(
        managed_dependency_source, target_is_directory=True
    )
    managed_dependency_copy = dependency_root / "copydep"
    managed_dependency_copy.mkdir(parents=True, exist_ok=True)
    (managed_dependency_copy / "README.md").write_text("copy", encoding="utf-8")

    scipkg.save_workspace_manifest(
        repo_dir,
        {
            "version": 1,
            "package_id": "maxwelllink",
            "linked_entries": [
                {
                    "name": "skills",
                    "mode": "symlink",
                    "source": str(managed_entry_source.resolve()),
                },
                {
                    "name": "public",
                    "mode": "copy",
                    "source": str(managed_entry_source.resolve()),
                },
                {
                    "name": "foreign-link",
                    "mode": "symlink",
                    "source": str(foreign_manifest_source.resolve()),
                },
            ],
            "linked_dependency_packages": [
                {
                    "package_id": "deppkg",
                    "mode": "symlink",
                    "source": str(managed_dependency_source.resolve()),
                },
                {
                    "package_id": "copydep",
                    "mode": "copy",
                    "source": str(managed_dependency_source.resolve()),
                },
            ],
        },
    )

    cli._cleanup_exec_overlay_symlinks(repo_dir=repo_dir, workspace_root=repo_dir)

    assert not managed_entry_link.exists()
    assert not managed_copy_entry.exists()
    assert foreign_link.is_symlink()
    assert user_link.is_symlink()
    assert unmanaged_copy_entry.exists()
    assert not managed_dependency_link.exists()
    assert not managed_dependency_copy.exists()
    assert not dependency_root.exists()


def test_ensure_exec_repo_ready_fails_when_git_missing_and_no_init(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    dummy_runner = SimpleNamespace(
        _is_valid_git_repo=lambda _path: False,
        _ensure_git_repo=lambda _path: None,
        _resolve_source_dir=lambda: tmp_path / "software",
        _ensure_template_agents_file=lambda _src, _dst: None,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: dummy_runner)
    args = SimpleNamespace(init_git=False, no_init_git=True)

    with pytest.raises(cli.PackageError, match="not a git repository"):
        cli._ensure_exec_repo_ready(repo_dir, args)


def test_ensure_exec_repo_ready_auto_initializes_git_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    source_dir = tmp_path / "software"
    source_dir.mkdir(parents=True, exist_ok=True)

    state = {"is_valid_repo": False}
    ensure_calls: list[Path] = []
    template_calls: list[tuple[Path, Path]] = []

    def fake_ensure_git_repo(path: Path) -> None:
        ensure_calls.append(path)
        state["is_valid_repo"] = True

    dummy_runner = SimpleNamespace(
        _is_valid_git_repo=lambda _path: bool(state["is_valid_repo"]),
        _ensure_git_repo=fake_ensure_git_repo,
        _resolve_source_dir=lambda: source_dir,
        _ensure_template_agents_file=lambda src, dst: template_calls.append(
            (Path(src), Path(dst))
        ),
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: dummy_runner)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt="": pytest.fail("interactive git-init prompt should not run"),
    )
    args = SimpleNamespace(init_git=False, no_init_git=False)

    cli._ensure_exec_repo_ready(repo_dir, args)

    assert ensure_calls == [repo_dir]
    assert template_calls == [(source_dir, repo_dir)]
    output = capsys.readouterr().out
    assert "Auto-initializing with `git init` by default" in output
    assert "`--init-git`" in output
    assert "`--no-init-git`" in output


def test_exec_parser_supports_package_pin_and_git_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "exec",
            "run",
            "test",
            "--package",
            "maxwelllink",
            "--init-git",
            "--sandbox",
            "read-only",
        ]
    )
    assert args.package_id == "maxwelllink"
    assert args.init_git is True
    assert args.sandbox == "read-only"
    assert args.hpc_profile is None


def test_exec_parser_accepts_hpc_profile() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        ["exec", "run test", "--hpc-profile", "scripts/hpc_profile_anvil.json"]
    )
    assert args.hpc_profile == "scripts/hpc_profile_anvil.json"


def test_exec_accepts_prompt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "prompt.md").write_text("simulate one cavity", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_run_exec_provider_prompt",
        lambda **kwargs: captured.update(kwargs) or 0,
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    code = cli.main(["exec", "prompt.md"])
    assert code == 0
    assert "projects/memory.md" in str(captured["prompt"])
    assert "simulate one cavity" in str(captured["prompt"])


def test_exec_hpc_profile_appends_execution_target_constraints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "hpc_profile.json").write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=16 --ntasks-per-node=16",
                "slurm_resource_policy": "Use moderate resources",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_run_exec_provider_prompt",
        lambda **kwargs: captured.update(kwargs) or 0,
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    code = cli.main(
        ["exec", "simulate one cavity", "--hpc-profile", "hpc_profile.json"]
    )
    assert code == 0
    prompt = str(captured["prompt"])
    assert "Execution target constraints:" in prompt
    assert "execution_target: HPC SLURM." in prompt
    assert "slurm_default_partition: `shared`." in prompt
    assert "slurm_defaults: `--nodes=1 --ntasks=16 --ntasks-per-node=16`." in prompt
    assert "slurm_resource_policy: Use moderate resources." in prompt
    assert prompt.endswith("Current request/context:\nsimulate one cavity\n")
    assert prompt.find("Execution target constraints:") < prompt.find(
        "Current request/context:\n"
    )


def test_exec_hpc_profile_requires_lightweight_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "legacy_profile.json").write_text(
        json.dumps(
            {
                "version": 1,
                "cluster_name": "legacy",
                "scheduler": "slurm",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    code = cli.main(["exec", "hello", "--hpc-profile", "legacy_profile.json"])
    assert code == 2
    assert "missing required `slurm_default_partition`" in capsys.readouterr().err


def test_exec_uses_default_home_hpc_profile_when_flag_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    profile = home / "HPC_PROFILE.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(
            {
                "slurm_default_partition": "debug",
                "slurm_defaults": "--nodes=1 --ntasks=2 --time=00:30:00",
                "slurm_resource_policy": "Keep jobs small",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(cli, "resolve_scipkg_root", lambda: tmp_path / "scipkg")
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "maxwelllink",
            "source": "default",
            "reason": "default_fallback",
            "note": "default_fallback",
        },
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: {
            "linked_count": 1,
            "collision_count": 0,
            "linked_dependency_count": 0,
        },
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        cli,
        "_run_exec_provider_prompt",
        lambda **kwargs: captured.update(kwargs) or 0,
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    assert cli.main(["exec", "simulate one cavity"]) == 0
    prompt = str(captured["prompt"])
    assert "Execution target constraints:" in prompt
    assert "execution_target: HPC SLURM." in prompt
    assert "slurm_default_partition: `debug`." in prompt
    assert "slurm_defaults: `--nodes=1 --ntasks=2 --time=00:30:00`." in prompt
    assert "slurm_resource_policy: Keep jobs small." in prompt


def test_exec_default_home_hpc_profile_requires_valid_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    profile = home / "HPC_PROFILE.json"
    profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    assert cli.main(["exec", "simulate one cavity"]) == 2
    assert "Default HPC profile missing required `slurm_resource_policy`" in (
        capsys.readouterr().err
    )


def test_exec_rejects_pdf_prompt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "prompt.pdf").write_bytes(b"%PDF-1.7\n")

    code = cli.main(["exec", "prompt.pdf"])
    assert code == 2
    assert "PDF prompt files are not supported yet" in capsys.readouterr().err


def test_run_exec_provider_prompt_uses_runner_sanitized_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: False)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(stdout=io.StringIO(""), stderr=io.StringIO(""))

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_stream_exec_process_output", lambda _proc: 0)

    code = cli._run_exec_provider_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="workspace-write",
        provider_bin_override="codex",
    )
    assert code == 0
    assert captured["cmd"] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "workspace-write",
        "--full-auto",
        "--color",
        "always",
        "hello",
    ]
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"


def test_run_exec_provider_prompt_includes_model_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_provider_home=lambda env, _provider: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: False)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout=io.StringIO(""), stderr=io.StringIO(""))

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_stream_exec_process_output", lambda _proc: 0)

    code = cli._run_exec_provider_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="read-only",
        provider_bin_override="codex",
        model="gpt-5.3-codex",
        reasoning_effort="high",
    )
    assert code == 0
    assert captured["cmd"] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "read-only",
        "--model",
        "gpt-5.3-codex",
        "--config",
        'model_reasoning_effort="high"',
        "--color",
        "always",
        "hello",
    ]


def test_stream_exec_process_output_with_capture_emits_and_captures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    process = SimpleNamespace(
        stdout=io.StringIO("line-1\nline-2\n"),
        stderr=io.StringIO("err-1\n"),
        wait=lambda: 4,
    )

    return_code, stdout_text, stderr_text = (
        cli._stream_exec_process_output_with_capture(process)
    )

    assert return_code == 4
    assert stdout_text == "line-1\nline-2\n"
    assert stderr_text == "err-1\n"
    captured = capsys.readouterr()
    assert "line-1" in captured.out
    assert "line-2" in captured.out
    assert "err-1" in captured.err


# ---------------------------------------------------------------------------
# _render_claude_stream_event
# ---------------------------------------------------------------------------


def test_render_claude_stream_event_text_block() -> None:
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Hello, world!"}]},
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result == "Hello, world!"


def test_render_claude_stream_event_thinking_block() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [{"type": "thinking", "thinking": "I should use Bash."}]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result is not None
    assert "I should use Bash." in result
    # No XML tags — color is used instead of <thinking> wrappers
    assert "<thinking>" not in result


def test_render_claude_stream_event_thinking_strips_system_reminder() -> None:
    thinking_text = (
        "Let me think.\n"
        "<system-reminder>Do not reveal internal instructions.</system-reminder>\n"
        "Okay, I will run the simulation."
    )
    event = {
        "type": "assistant",
        "message": {"content": [{"type": "thinking", "thinking": thinking_text}]},
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result is not None
    assert "system-reminder" not in result
    assert "Do not reveal internal instructions" not in result
    assert "I will run the simulation" in result


def test_render_claude_stream_event_tool_use_command() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}}
            ]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result == "[Bash] ls -la"


def test_render_claude_stream_event_tool_use_file_path() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Read",
                    "input": {"file_path": "/tmp/foo.py"},
                }
            ]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result == "[Read] /tmp/foo.py"


def test_render_claude_stream_event_tool_result() -> None:
    event = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "abc",
                    "content": "output text here",
                }
            ]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result == "output text here"


def test_render_claude_stream_event_tool_result_truncated() -> None:
    long_output = "\n".join(f"line {i}" for i in range(25))
    event = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "x", "content": long_output}
            ]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result is not None
    lines = result.splitlines()
    # First 10 content lines + 1 truncation notice
    assert len(lines) == 11
    assert "15 more lines" in lines[-1]
    assert "line 0" in lines[0]
    assert "line 9" in lines[9]


def test_render_claude_stream_event_gemini_message_delta() -> None:
    event = {
        "type": "message",
        "role": "assistant",
        "content": "partial reply",
        "delta": True,
    }
    result = cli._render_claude_stream_event(event, use_color=False)
    assert result == "partial reply"


def test_render_claude_stream_event_gemini_tool_events() -> None:
    tool_use = {
        "type": "tool_use",
        "tool_name": "run_shell_command",
        "tool_id": "tool-1",
        "parameters": {"command": "ls -la"},
    }
    tool_result = {
        "type": "tool_result",
        "tool_id": "tool-1",
        "status": "success",
        "output": "line-1\nline-2",
    }

    use_rendered = cli._render_claude_stream_event(tool_use, use_color=False)
    result_rendered = cli._render_claude_stream_event(tool_result, use_color=False)
    assert use_rendered == "[run_shell_command] ls -la"
    assert result_rendered == "line-1\nline-2"


def test_render_claude_stream_event_system_returns_none() -> None:
    assert (
        cli._render_claude_stream_event({"type": "system", "subtype": "init"}) is None
    )


def test_render_claude_stream_event_result_returns_none() -> None:
    assert (
        cli._render_claude_stream_event({"type": "result", "subtype": "success"})
        is None
    )


def test_render_claude_stream_event_empty_content_returns_none() -> None:
    assert (
        cli._render_claude_stream_event(
            {"type": "assistant", "message": {"content": []}}
        )
        is None
    )


def test_render_claude_stream_event_applies_ansi_colors() -> None:
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "thinking", "thinking": "considering"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "echo hi"}},
                {"type": "text", "text": "done"},
            ]
        },
    }
    result = cli._render_claude_stream_event(event, use_color=True)
    assert result is not None
    # ANSI escape sequences must be present
    assert "\033[" in result
    # Content must still be present
    assert "considering" in result
    assert "Bash" in result
    assert "echo hi" in result
    assert "done" in result


def test_stream_claude_exec_output_renders_events(
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Running sim"}]},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "python sim.py"},
                        }
                    ]
                },
            }
        ),
        json.dumps({"type": "result", "subtype": "success", "result": "done"}),
    ]
    process = SimpleNamespace(
        stdout=io.StringIO("\n".join(events) + "\n"),
        stderr=io.StringIO(""),
        wait=lambda: 0,
    )
    return_code = cli._stream_claude_exec_output(process)
    assert return_code == 0
    captured = capsys.readouterr()
    assert "Running sim" in captured.out
    assert "[Bash] python sim.py" in captured.out


def test_stream_claude_exec_output_handles_keyboard_interrupt() -> None:
    """KeyboardInterrupt during wait must terminate the child and return 130."""
    terminated: list[bool] = []
    waited: list[bool] = []

    class FakeProcess:
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def poll(self):
            # Never finish on its own — simulates a hanging process.
            return None

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout=None):
            waited.append(True)
            return 130

        def kill(self):
            pass

    import fermilink.cli.exec_runtime as _rt

    original_wait = _rt._wait_process_with_optional_stop

    def _raise_keyboard_interrupt(process, **_kwargs):
        raise KeyboardInterrupt

    _rt._wait_process_with_optional_stop = _raise_keyboard_interrupt
    try:
        return_code = cli._stream_claude_exec_output(FakeProcess())
    finally:
        _rt._wait_process_with_optional_stop = original_wait

    assert return_code == 130
    assert terminated == [True]


def test_stream_claude_exec_output_with_capture_renders_and_captures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Running sim"}]},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {"command": "python sim.py"},
                        }
                    ]
                },
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "<wait_seconds>5</wait_seconds>"}
                    ]
                },
            }
        ),
    ]
    process = SimpleNamespace(
        stdout=io.StringIO("\n".join(events) + "\n"),
        stderr=io.StringIO("warning-line\n"),
        wait=lambda: 0,
    )
    return_code, assistant_text, stderr_text = (
        cli._stream_claude_exec_output_with_capture(process)
    )
    assert return_code == 0
    assert assistant_text == "Running sim\n<wait_seconds>5</wait_seconds>"
    assert stderr_text == "warning-line\n"
    captured = capsys.readouterr()
    assert "Running sim" in captured.out
    assert "[Bash] python sim.py" in captured.out
    assert "warning-line" in captured.err


def test_stream_claude_exec_output_with_capture_gemini_delta_preserves_tags() -> None:
    events = [
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": "<wait_seconds>",
                "delta": True,
            }
        ),
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": "5",
                "delta": True,
            }
        ),
        json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": "</wait_seconds>",
                "delta": True,
            }
        ),
    ]
    process = SimpleNamespace(
        stdout=io.StringIO("\n".join(events) + "\n"),
        stderr=io.StringIO(""),
        wait=lambda: 0,
    )
    return_code, assistant_text, stderr_text = (
        cli._stream_claude_exec_output_with_capture(process)
    )
    assert return_code == 0
    assert assistant_text == "<wait_seconds>5</wait_seconds>"
    assert stderr_text == ""


def test_stream_claude_exec_output_with_capture_handles_keyboard_interrupt() -> None:
    """KeyboardInterrupt during wait must terminate the child and return 130."""
    terminated: list[bool] = []

    class FakeProcess:
        stdout = io.StringIO("")
        stderr = io.StringIO("")

        def poll(self):
            return None

        def terminate(self):
            terminated.append(True)

        def wait(self, timeout=None):
            return 130

        def kill(self):
            pass

    import fermilink.cli.exec_runtime as _rt

    original_wait = _rt._wait_process_with_optional_stop

    def _raise_keyboard_interrupt(process, **_kwargs):
        raise KeyboardInterrupt

    _rt._wait_process_with_optional_stop = _raise_keyboard_interrupt
    try:
        return_code, assistant_text, stderr_text = (
            cli._stream_claude_exec_output_with_capture(FakeProcess())
        )
    finally:
        _rt._wait_process_with_optional_stop = original_wait

    assert return_code == 130
    assert assistant_text == ""
    assert stderr_text == ""
    assert terminated == [True]


def test_run_exec_provider_prompt_uses_devnull_stdin_for_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_provider_home=lambda env, _provider: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: True)

    def fake_popen(cmd, **kwargs):
        captured["stdin"] = kwargs.get("stdin")
        return SimpleNamespace(
            stdout=io.StringIO(""),
            stderr=io.StringIO(""),
            wait=lambda: 0,
        )

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    cli._run_exec_provider_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox=None,
        provider_bin_override="claude",
        provider="claude",
        sandbox_policy="bypass",
    )
    assert captured["stdin"] is cli.subprocess.DEVNULL


def test_run_exec_provider_prompt_uses_json_stream_for_claude(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_provider_home=lambda env, _provider: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: True)

    event = json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": "done"}]},
        }
    )

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(
            stdout=io.StringIO(event + "\n"),
            stderr=io.StringIO(""),
            wait=lambda: 0,
        )

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("subprocess.run should not be called for claude")
        ),
    )

    code = cli._run_exec_provider_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox=None,
        provider_bin_override="claude",
        provider="claude",
        sandbox_policy="bypass",
    )
    assert code == 0
    # stream-json flags must be present for claude
    cmd = captured["cmd"]
    assert "--output-format" in cmd
    assert "stream-json" in cmd
    captured_out = capsys.readouterr()
    assert "done" in captured_out.out


def test_prepare_provider_runtime_env_gemini_thinking_level_and_cleanup() -> None:
    env, temp_paths = cli._prepare_provider_runtime_env(
        {"BASE": "1"},
        provider="gemini",
        model="gemini-3.0-pro",
        reasoning_effort="medium",
    )
    settings_value = env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
    assert isinstance(settings_value, str)
    settings_path = Path(settings_value)
    assert settings_path.exists()
    assert temp_paths == [settings_path]

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    config = payload["modelConfigs"]["customOverrides"][0]["modelConfig"][
        "generateContentConfig"
    ]["thinkingConfig"]
    assert config["includeThoughts"] is True
    assert config["thinkingLevel"] == "MEDIUM"
    assert "thinkingBudget" not in config

    cli._cleanup_temp_paths(temp_paths)
    assert not settings_path.exists()


def test_prepare_provider_runtime_env_gemini_thinking_budget_and_cleanup() -> None:
    env, temp_paths = cli._prepare_provider_runtime_env(
        {},
        provider="gemini",
        model="gemini-2.5-pro",
        reasoning_effort="xhigh",
    )
    settings_value = env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
    assert isinstance(settings_value, str)
    settings_path = Path(settings_value)
    assert settings_path.exists()

    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    config = payload["modelConfigs"]["customOverrides"][0]["modelConfig"][
        "generateContentConfig"
    ]["thinkingConfig"]
    assert config["includeThoughts"] is True
    assert config["thinkingBudget"] == 16384
    assert "thinkingLevel" not in config

    cli._cleanup_temp_paths(temp_paths)
    assert not settings_path.exists()


def test_run_exec_provider_prompt_gemini_applies_reasoning_env_and_cleans_temp_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    settings_path_holder: dict[str, Path] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_provider_home=lambda env, _provider: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: True)

    event = json.dumps(
        {
            "type": "message",
            "role": "assistant",
            "content": "gemini done",
            "delta": True,
        }
    )

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        env = kwargs.get("env")
        captured["env"] = env
        assert isinstance(env, dict)
        settings_value = env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
        assert isinstance(settings_value, str)
        settings_path = Path(settings_value)
        settings_path_holder["path"] = settings_path
        assert settings_path.exists()
        return SimpleNamespace(
            stdout=io.StringIO(event + "\n"),
            stderr=io.StringIO(""),
            wait=lambda: 0,
        )

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    code = cli._run_exec_provider_prompt(
        repo_dir=tmp_path,
        prompt="hello gemini",
        sandbox="read-only",
        provider_bin_override="gemini",
        provider="gemini",
        sandbox_policy="enforce",
        model="gemini-3.0-pro",
        reasoning_effort="high",
    )
    assert code == 0
    command = captured["cmd"]
    assert isinstance(command, list)
    assert command[0] == "gemini"
    assert "--output-format" in command
    assert "stream-json" in command
    assert "--sandbox" in command
    assert "--approval-mode" in command
    approval_idx = command.index("--approval-mode")
    assert command[approval_idx + 1] == "plan"
    assert "--model" in command
    model_idx = command.index("--model")
    assert command[model_idx + 1] == "gemini-3.0-pro"

    settings_path = settings_path_holder["path"]
    assert isinstance(settings_path, Path)
    assert not settings_path.exists()


def test_run_exec_provider_prompt_uses_direct_terminal_stream_when_tty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: object())
    monkeypatch.setattr(
        cli,
        "_collect_second_guess_assistant_text",
        lambda raw_stream_text, *, web_app: f"assistant:{raw_stream_text.count('agent_message')}",
    )

    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO("codex streaming line\n")
            self.stderr = io.StringIO("warning-line\n")

        def wait(self) -> int:
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        output_index = cmd.index("--output-last-message")
        Path(cmd[output_index + 1]).write_text(
            "assistant from file\n", encoding="utf-8"
        )
        return FakeProcess()

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: False)

    result = cli._run_exec_chat_turn(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="workspace-write",
        provider_bin_override="codex",
        provider="codex",
        sandbox_policy="enforce",
    )

    assert result["assistant_text"] == "assistant from file"
    assert result["return_code"] == 0
    assert result["stderr"] == "warning-line"
    assert captured["cwd"] == str(tmp_path)
    command = captured["cmd"]
    assert isinstance(command, list)
    assert command[:6] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "workspace-write",
    ]
    assert "--color" in command
    color_index = command.index("--color")
    assert command[color_index + 1] == "always"
    assert "--output-last-message" in command
    assert command[-1] == "hello"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"

    streamed = capsys.readouterr()
    assert "codex streaming line" in streamed.out
    assert "warning-line" in streamed.err


def test_run_exec_chat_turn_uses_direct_terminal_stream_and_output_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: True)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Popen should not run in tty mode")
        ),
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        output_index = cmd.index("--output-last-message")
        Path(cmd[output_index + 1]).write_text("tty assistant\n", encoding="utf-8")
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._run_exec_chat_turn(
        repo_dir=tmp_path,
        prompt="hello tty",
        sandbox="read-only",
        provider_bin_override="codex",
        provider="codex",
        sandbox_policy="enforce",
    )
    assert result["assistant_text"] == "tty assistant"
    assert result["return_code"] == 3
    assert result["stderr"] == ""
    assert captured["cwd"] == str(tmp_path)

    command = captured["cmd"]
    assert isinstance(command, list)
    assert command[:6] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "read-only",
    ]
    assert "--color" in command
    color_index = command.index("--color")
    assert command[color_index + 1] == "always"
    assert "--output-last-message" in command
    assert command[-1] == "hello tty"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"


def test_run_exec_chat_turn_claude_streams_and_captures_assistant_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: True)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("subprocess.run should not be called for claude chat turns")
        ),
    )

    events = [
        json.dumps(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "loop step done"}]},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "<wait_seconds>3</wait_seconds>"}
                    ]
                },
            }
        ),
    ]

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        captured["env"] = kwargs.get("env")
        captured["stdin"] = kwargs.get("stdin")
        return SimpleNamespace(
            stdout=io.StringIO("\n".join(events) + "\n"),
            stderr=io.StringIO("warning-line\n"),
            wait=lambda: 0,
        )

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    result = cli._run_exec_chat_turn(
        repo_dir=tmp_path,
        prompt="hello claude",
        sandbox="workspace-write",
        provider_bin_override="claude",
        provider="claude",
        sandbox_policy="enforce",
    )
    assert result["assistant_text"] == "loop step done\n<wait_seconds>3</wait_seconds>"
    assert result["return_code"] == 0
    assert result["stderr"] == "warning-line"
    assert captured["cwd"] == str(tmp_path)
    assert captured["stdin"] is cli.subprocess.DEVNULL
    command = captured["cmd"]
    assert isinstance(command, list)
    assert "--output-format" in command
    output_index = command.index("--output-format")
    assert command[output_index + 1] == "stream-json"
    assert "--output-last-message" not in command
    assert command[-1] == "hello claude"
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"


def test_run_exec_second_guess_uses_runner_sanitized_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)

    web_app = SimpleNamespace(
        _build_package_catalog=lambda **_kwargs: [{"id": "maxwelllink"}],
        _build_second_guess_prompt=lambda **_kwargs: "route prompt",
        _extract_first_json_object=lambda text: json.loads(text),
        _normalize_package_id_safe=lambda value: (
            value if isinstance(value, str) else None
        ),
        _coerce_confidence=lambda value: float(value),
        PACKAGE_SOURCE_SECOND_GUESS="second_guess",
    )
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: web_app)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(
            returncode=0,
            stdout='{"route":"keep","package_id":"maxwelllink","confidence":0.91,"reason":"ok"}',
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._run_exec_second_guess(
        user_text="simulate cavity",
        repo_dir=tmp_path,
        scipkg_root=tmp_path / "scientific_packages",
        package_ids=["maxwelllink", "otherpkg"],
        active_package_id="maxwelllink",
        base_package_id="maxwelllink",
        provider="codex",
        provider_bin="codex",
        sandbox_policy="enforce",
    )
    assert result["package_id"] == "maxwelllink"
    assert result["switched"] is False
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"


def test_run_exec_second_guess_claude_provider_runs_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_provider_home=lambda env, _provider: {
            **env,
            "CODEX_HOME_NORMALIZED": "1",
        },
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)

    web_app = SimpleNamespace(
        _build_package_catalog=lambda **_kwargs: [{"id": "maxwelllink"}],
        _build_second_guess_prompt=lambda **_kwargs: "route prompt",
        _extract_first_json_object=lambda text: json.loads(text),
        _extract_text=lambda _event: None,
        _normalize_package_id_safe=lambda value: (
            value if isinstance(value, str) else None
        ),
        _coerce_confidence=lambda value: float(value),
        PACKAGE_SOURCE_SECOND_GUESS="second_guess",
    )
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: web_app)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(
            returncode=0,
            stdout='{"route":"keep","package_id":"maxwelllink","confidence":0.95,"reason":"correct"}',
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._run_exec_second_guess(
        user_text="simulate cavity",
        repo_dir=tmp_path,
        scipkg_root=tmp_path / "scientific_packages",
        package_ids=["maxwelllink", "otherpkg"],
        active_package_id="maxwelllink",
        base_package_id="maxwelllink",
        provider="claude",
        provider_bin="claude",
        sandbox_policy="bypass",
    )
    assert result["package_id"] == "maxwelllink"
    assert result["switched"] is False
    assert "second_guess_keep" in result["note"]
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "claude" in cmd[0]
    # plain-text output (no stream-json) for non-codex provider
    assert "--output-format" not in cmd


def test_run_exec_second_guess_gemini_applies_reasoning_env_and_cleans_temp_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    settings_path_holder: dict[str, Path] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_provider_home=lambda env, _provider: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)

    web_app = SimpleNamespace(
        _build_package_catalog=lambda **_kwargs: [{"id": "maxwelllink"}],
        _build_second_guess_prompt=lambda **_kwargs: "route prompt",
        _extract_first_json_object=lambda text: json.loads(text),
        _extract_text=lambda _event: None,
        _normalize_package_id_safe=lambda value: (
            value if isinstance(value, str) else None
        ),
        _coerce_confidence=lambda value: float(value),
        PACKAGE_SOURCE_SECOND_GUESS="second_guess",
    )
    monkeypatch.setattr(cli, "_load_web_router_module", lambda: web_app)

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        env = kwargs.get("env")
        captured["env"] = env
        assert isinstance(env, dict)
        settings_value = env.get("GEMINI_CLI_SYSTEM_SETTINGS_PATH")
        assert isinstance(settings_value, str)
        settings_path = Path(settings_value)
        settings_path_holder["path"] = settings_path
        assert settings_path.exists()
        return SimpleNamespace(
            returncode=0,
            stdout='{"route":"keep","package_id":"maxwelllink","confidence":0.95,"reason":"correct"}',
            stderr="",
        )

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    result = cli._run_exec_second_guess(
        user_text="simulate cavity",
        repo_dir=tmp_path,
        scipkg_root=tmp_path / "scientific_packages",
        package_ids=["maxwelllink", "otherpkg"],
        active_package_id="maxwelllink",
        base_package_id="maxwelllink",
        provider="gemini",
        provider_bin="gemini",
        sandbox_policy="enforce",
        model="gemini-2.5-pro",
        reasoning_effort="high",
    )
    assert result["package_id"] == "maxwelllink"
    assert result["switched"] is False
    assert "second_guess_keep" in result["note"]
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "gemini"
    assert "--sandbox" in cmd
    assert "--approval-mode" in cmd
    approval_idx = cmd.index("--approval-mode")
    assert cmd[approval_idx + 1] == "plan"

    settings_path = settings_path_holder["path"]
    assert isinstance(settings_path, Path)
    assert not settings_path.exists()


def test_filter_exec_overlay_package_meta_excludes_public_from_explicit_entries() -> (
    None
):
    package_meta = {
        "installed_path": "/tmp/fake",
        "overlay_entries": ["skills", "public", "docs"],
    }

    filtered = cli._filter_exec_overlay_package_meta(package_meta)

    assert filtered["overlay_entries"] == ["skills", "docs"]


def test_filter_exec_overlay_package_meta_excludes_public_when_overlay_is_unset(
    tmp_path: Path,
) -> None:
    package_root = tmp_path / "pkg"
    package_root.mkdir()
    (package_root / "public").mkdir()
    (package_root / "skills").mkdir()
    (package_root / "docs").mkdir()

    package_meta = {"installed_path": str(package_root)}
    filtered = cli._filter_exec_overlay_package_meta(package_meta)
    entries = filtered.get("overlay_entries")

    assert isinstance(entries, list)
    assert "public" not in entries
    assert "skills" in entries
    assert "docs" in entries


def test_load_web_router_module_sets_router_only_import_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cli._load_web_router_module.cache_clear()
    monkeypatch.delenv(cli.WEB_ROUTER_ONLY_IMPORT_ENV, raising=False)

    sentinel = object()
    captured: dict[str, object] = {}

    def fake_import_module(name: str) -> object:
        captured["name"] = name
        captured["env"] = cli.os.getenv(cli.WEB_ROUTER_ONLY_IMPORT_ENV)
        return sentinel

    monkeypatch.setattr(cli.importlib, "import_module", fake_import_module)

    loaded = cli._load_web_router_module()

    assert loaded is sentinel
    assert captured["name"] == "fermilink.web.app"
    assert captured["env"] == "1"
    cli._load_web_router_module.cache_clear()
