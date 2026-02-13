from __future__ import annotations

import io
import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from fermilink import cli


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

    def fake_run_exec(*, repo_dir: Path, prompt: str, sandbox: str, codex_bin: str) -> int:
        calls["repo_dir"] = repo_dir
        calls["prompt"] = prompt
        calls["sandbox"] = sandbox
        calls["codex_bin"] = codex_bin
        return 0

    monkeypatch.setattr(cli, "_run_exec_codex_prompt", fake_run_exec)

    code = cli.main(["exec", "simulate", "a", "cavity", "--sandbox", "workspace-write"])
    assert code == 0
    assert calls["repo_dir"] == repo_dir
    assert calls["prompt"] == "simulate a cavity"
    assert calls["sandbox"] == "workspace-write"

    output = capsys.readouterr().out
    assert "[package] Using maxwelllink (selection: second_guess)" in output
    assert "[overlay] linked entries: 5, linked dependencies: 2, collisions: 1" in output


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
        lambda **_kwargs: {"linked_count": 1, "collision_count": 0, "linked_dependency_count": 0},
    )
    monkeypatch.setattr(cli, "_run_exec_codex_prompt", lambda **_kwargs: 7)

    code = cli.main(["exec", "hello"])
    assert code == 7


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
        "hello",
    ]
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
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("Popen should not run in tty mode")),
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
        _normalize_package_id_safe=lambda value: value if isinstance(value, str) else None,
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
        codex_bin="codex",
    )
    assert result["package_id"] == "maxwelllink"
    assert result["switched"] is False
    env = captured["env"]
    assert isinstance(env, dict)
    assert env.get("SANITIZED") == "1"
    assert env.get("CODEX_HOME_NORMALIZED") == "1"
