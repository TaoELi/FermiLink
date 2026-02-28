from __future__ import annotations

import io
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
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
        *, repo_dir: Path, prompt: str, sandbox: str, codex_bin: str, **_kwargs
    ) -> int:
        calls["repo_dir"] = repo_dir
        calls["prompt"] = prompt
        calls["sandbox"] = sandbox
        calls["codex_bin"] = codex_bin
        calls["model"] = _kwargs.get("model")
        calls["reasoning_effort"] = _kwargs.get("reasoning_effort")
        return 0

    monkeypatch.setattr(cli, "_run_exec_codex_prompt", fake_run_exec)

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
    monkeypatch.setattr(cli, "_run_exec_codex_prompt", lambda **_kwargs: 7)
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
        "_run_exec_codex_prompt",
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
        "_run_exec_codex_prompt",
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


def test_run_exec_codex_prompt_uses_runner_sanitized_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_codex_home=lambda env: {**env, "CODEX_HOME_NORMALIZED": "1"},
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: False)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return SimpleNamespace(stdout=io.StringIO(""), stderr=io.StringIO(""))

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_stream_exec_process_output", lambda _proc: 0)

    code = cli._run_exec_codex_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="workspace-write",
        codex_bin="codex",
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


def test_run_exec_codex_prompt_includes_model_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: env,
        _normalize_codex_home=lambda env: env,
    )
    monkeypatch.setattr(cli, "_load_runner_app_module", lambda: runner_app)
    monkeypatch.setattr(cli, "_should_use_direct_terminal_stream", lambda: False)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(stdout=io.StringIO(""), stderr=io.StringIO(""))

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli, "_stream_exec_process_output", lambda _proc: 0)

    code = cli._run_exec_codex_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="read-only",
        codex_bin="codex",
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


def test_run_exec_chat_turn_streams_and_collects_assistant_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_codex_home=lambda env: {**env, "CODEX_HOME_NORMALIZED": "1"},
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
        codex_bin="codex",
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
        _normalize_codex_home=lambda env: {**env, "CODEX_HOME_NORMALIZED": "1"},
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
        codex_bin="codex",
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


def test_run_exec_codex_prompt_uses_direct_terminal_stream_when_tty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    runner_app = SimpleNamespace(
        _sanitize_env=lambda env: {**env, "SANITIZED": "1"},
        _normalize_codex_home=lambda env: {**env, "CODEX_HOME_NORMALIZED": "1"},
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
        captured["env"] = kwargs.get("env")
        captured["cwd"] = kwargs.get("cwd")
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    code = cli._run_exec_codex_prompt(
        repo_dir=tmp_path,
        prompt="hello",
        sandbox="read-only",
        codex_bin="codex",
    )
    assert code == 3
    assert captured["cmd"] == [
        "codex",
        "exec",
        "--cd",
        str(tmp_path),
        "--sandbox",
        "read-only",
        "--color",
        "always",
        "hello",
    ]
    assert captured["cwd"] == str(tmp_path)
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
        _normalize_codex_home=lambda env: {**env, "CODEX_HOME_NORMALIZED": "1"},
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


def test_run_exec_second_guess_skips_non_codex_provider(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_run(*_args, **_kwargs):
        raise AssertionError("subprocess.run should not execute for non-codex")

    monkeypatch.setattr(cli.subprocess, "run", fail_run)

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
    assert result == {
        "package_id": "maxwelllink",
        "source": "default",
        "switched": False,
        "note": "second_guess_provider_not_implemented",
    }


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
