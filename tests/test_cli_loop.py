from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli.commands import sessions as session_commands
from fermilink.cli.commands import workflows as workflow_commands


def test_loop_reads_prompt_file_and_initializes_memory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    (repo_dir / "prompt.md").write_text("do the thing", encoding="utf-8")

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    def fake_run_chat_turn(**kwargs):
        captured.update(kwargs)
        return {"assistant_text": "ok", "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)

    cleanup_calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        cli,
        "_cleanup_exec_overlay_symlinks",
        lambda *, repo_dir, workspace_root: cleanup_calls.append(
            (repo_dir, workspace_root)
        ),
    )

    code = cli.main(["loop", "--max-iterations", "1", "prompt.md"])
    assert code == 1

    memory_path = repo_dir / "projects" / "memory.md"
    assert memory_path.exists()
    memory = memory_path.read_text(encoding="utf-8")
    assert "do the thing" in memory
    assert "## Short-Term Memory (Operational)" in memory
    assert "### Plan" in memory
    assert "### Progress log" in memory
    assert "## Long-Term Memory (Persistent)" in memory
    assert "### File map" in memory
    assert "### Simulation history" in memory
    assert "### Key results" in memory
    assert "### Parameter source mapping" in memory
    assert "### Simulation uncertainty" in memory
    assert "### Suggested skills updates" in memory

    assert "projects/memory.md" in str(captured.get("prompt"))
    assert "do the thing" in str(captured.get("prompt"))

    assert cleanup_calls
    out = capsys.readouterr().out
    assert "[loop] memory: projects/memory.md" in out


def test_loop_emits_done_token_when_present_in_last_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(
        cli,
        "_run_exec_chat_turn",
        lambda **_kwargs: {
            "assistant_text": f"all done\n{cli.LOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        },
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    code = cli.main(["loop", "finish it"])
    assert code == 0
    out = capsys.readouterr().out
    assert cli.LOOP_DONE_TOKEN in out


def test_loop_parser_supports_package_pin_and_git_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "loop",
            "hello",
            "--package",
            "maxwelllink",
            "--init-git",
            "--sandbox",
            "workspace-write",
        ]
    )
    assert args.package_id == "maxwelllink"
    assert args.init_git is True
    assert args.sandbox == "workspace-write"
    assert args.max_iterations == 10
    assert args.wait_seconds == 1.0
    assert args.max_wait_seconds == 6000.0
    assert args.pid_stall_seconds == 900.0
    assert args.hpc_profile is None


def test_loop_attempts_completion_checkpoint_commit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(
        cli,
        "_run_exec_chat_turn",
        lambda **_kwargs: {
            "assistant_text": f"{cli.LOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        },
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    completion_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        cli,
        "_workflow_completion_commit",
        lambda *, repo_dir, mode_name: completion_calls.append(
            (Path(repo_dir), str(mode_name))
        )
        or {"status": "noop", "sha": "", "error": ""},
    )

    code = cli.main(["loop", "finish it"])
    assert code == 0
    assert completion_calls == [(repo_dir, "loop")]


def test_loop_parser_accepts_hpc_profile() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        ["loop", "hello", "--hpc-profile", "scripts/hpc_profile_anvil.json"]
    )
    assert args.hpc_profile == "scripts/hpc_profile_anvil.json"


def test_loop_hpc_profile_appends_execution_target_constraints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)
    (repo_dir / "hpc_profile.json").write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1",
                "slurm_resource_policy": "Use single-node defaults unless MPI is required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
        "_run_exec_chat_turn",
        lambda **kwargs: captured.update(kwargs)
        or {
            "assistant_text": f"{cli.LOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        },
    )
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    code = cli.main(["loop", "--hpc-profile", "hpc_profile.json", "finish it"])
    assert code == 0
    prompt = str(captured.get("prompt") or "")
    assert "Execution target constraints:" in prompt
    assert "execution_target: HPC SLURM." in prompt
    assert "slurm_default_partition: `shared`." in prompt
    assert "slurm_defaults: `--nodes=1 --ntasks=1 --ntasks-per-node=1`." in prompt
    assert (
        "slurm_resource_policy: Use single-node defaults unless MPI is required."
        in prompt
    )
    assert prompt.endswith("Original request:\nfinish it\n")
    assert prompt.find("Execution target constraints:") < prompt.find(
        "Original request:\n"
    )


def test_loop_hpc_profile_requires_lightweight_schema(
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
    code = cli.main(["loop", "finish it", "--hpc-profile", "legacy_profile.json"])
    assert code == 2
    assert "missing required `slurm_default_partition`" in capsys.readouterr().err


def test_resolve_exec_like_user_prompt_accepts_long_single_token_text() -> None:
    long_prompt = "x" * 5000
    text, prompt_file = cli._resolve_exec_like_user_prompt(
        cli.argparse.Namespace(prompt=[long_prompt], command="loop")
    )
    assert text == long_prompt
    assert prompt_file is None


def test_loop_wait_seconds_sleeps_between_iterations(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    run_calls: list[dict[str, object]] = []
    run_results = [
        {"assistant_text": "not done yet", "return_code": 0, "stderr": ""},
        {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""},
    ]

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        return run_results[len(run_calls) - 1]

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(float(seconds)))

    code = cli.main(
        ["loop", "--max-iterations", "2", "--wait-seconds", "3", "finish it"]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert slept == [3.0]


def test_loop_wait_seconds_uses_agent_tag_and_caps_by_max_wait(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    run_results = [
        {
            "assistant_text": "working\n<wait_seconds>120</wait_seconds>\n",
            "return_code": 0,
            "stderr": "",
        },
        {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""},
    ]
    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        return run_results[len(run_calls) - 1]

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(float(seconds)))

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "5",
            "--max-wait-seconds",
            "30",
            "finish it",
        ]
    )
    assert code == 0
    assert slept == [30.0]


def test_loop_waits_for_pid_tags_before_next_iteration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
    )
    run_calls: list[dict[str, object]] = []

    try:

        def fake_run_chat_turn(**kwargs):
            run_calls.append(kwargs)
            if len(run_calls) == 1:
                return {
                    "assistant_text": f"submitted\n<pid_number>{proc.pid}</pid_number>\n",
                    "return_code": 0,
                    "stderr": "",
                }
            assert proc.poll() is not None
            return {
                "assistant_text": cli.LOOP_DONE_TOKEN,
                "return_code": 0,
                "stderr": "",
            }

        monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
        monkeypatch.setattr(
            cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None
        )

        code = cli.main(
            [
                "loop",
                "--max-iterations",
                "2",
                "--wait-seconds",
                "0.02",
                "--max-wait-seconds",
                "2",
                "finish it",
            ]
        )
        assert code == 0
        assert len(run_calls) == 2
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)


def test_loop_pid_wait_respects_max_wait_cap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    run_calls: list[dict[str, object]] = []

    try:

        def fake_run_chat_turn(**kwargs):
            run_calls.append(kwargs)
            if len(run_calls) == 1:
                return {
                    "assistant_text": f"submitted\n<pid_number>{proc.pid}</pid_number>\n",
                    "return_code": 0,
                    "stderr": "",
                }
            assert proc.poll() is None
            return {
                "assistant_text": cli.LOOP_DONE_TOKEN,
                "return_code": 0,
                "stderr": "",
            }

        monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
        monkeypatch.setattr(
            cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None
        )

        code = cli.main(
            [
                "loop",
                "--max-iterations",
                "2",
                "--wait-seconds",
                "0.02",
                "--max-wait-seconds",
                "0.05",
                "finish it",
            ]
        )
        assert code == 0
        assert len(run_calls) == 2
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)


def test_loop_waits_for_pid_and_slurm_tags_before_next_iteration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2)"],
    )
    run_calls: list[dict[str, object]] = []
    slurm_poll_calls = {"count": 0}

    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)

    def fake_query_slurm_job_state(job_id: str) -> str:
        assert job_id == "12345"
        slurm_poll_calls["count"] += 1
        if slurm_poll_calls["count"] < 3:
            return "RUNNING"
        return "COMPLETED"

    monkeypatch.setattr(
        session_commands, "_query_slurm_job_state", fake_query_slurm_job_state
    )

    try:

        def fake_run_chat_turn(**kwargs):
            run_calls.append(kwargs)
            if len(run_calls) == 1:
                return {
                    "assistant_text": (
                        f"submitted\n<pid_number>{proc.pid}</pid_number>\n"
                        "<slurm_job_number>12345</slurm_job_number>\n"
                    ),
                    "return_code": 0,
                    "stderr": "",
                }
            assert proc.poll() is not None
            return {
                "assistant_text": cli.LOOP_DONE_TOKEN,
                "return_code": 0,
                "stderr": "",
            }

        monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
        monkeypatch.setattr(
            cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None
        )

        code = cli.main(
            [
                "loop",
                "--max-iterations",
                "2",
                "--wait-seconds",
                "0.02",
                "--max-wait-seconds",
                "2",
                "finish it",
            ]
        )
        assert code == 0
        assert len(run_calls) == 2
        assert slurm_poll_calls["count"] >= 3
    finally:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=2)


def test_loop_slurm_wait_skips_when_tools_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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

    run_calls: list[dict[str, object]] = []
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: False)
    monkeypatch.setattr(
        session_commands,
        "_query_slurm_job_state",
        lambda _job_id: (_ for _ in ()).throw(
            AssertionError("slurm query should not run when tools are unavailable")
        ),
    )

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": "submitted\n<slurm_job_number>12345</slurm_job_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    slept: list[float] = []
    monkeypatch.setattr(cli.time, "sleep", lambda seconds: slept.append(float(seconds)))

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "0.02",
            "--max-wait-seconds",
            "2",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert slept == []


def test_loop_pid_issue_breaks_mixed_wait_early(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)

    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": (
                    "submitted\n"
                    "<pid_number>101</pid_number>\n"
                    "<pid_number>102</pid_number>\n"
                    "<slurm_job_number>12345</slurm_job_number>\n"
                ),
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)

    slurm_poll_calls = {"count": 0}

    def fake_query_slurm_job_state(job_id: str) -> str:
        assert job_id == "12345"
        slurm_poll_calls["count"] += 1
        return "RUNNING"

    monkeypatch.setattr(
        session_commands, "_query_slurm_job_state", fake_query_slurm_job_state
    )

    pid_calls: dict[int, int] = {101: 0, 102: 0}

    def fake_query_pid_snapshot(pid: int):
        pid_calls[pid] = pid_calls.get(pid, 0) + 1
        if pid == 101:
            if pid_calls[pid] == 1:
                return session_commands._PidSnapshot(
                    pid=101, start_token="launch-a", cpu_seconds=1.0
                )
            return None
        return session_commands._PidSnapshot(
            pid=102,
            start_token="launch-b",
            cpu_seconds=float(pid_calls[pid]),
        )

    monkeypatch.setattr(
        session_commands, "_query_pid_snapshot", fake_query_pid_snapshot
    )

    clock = {"now": 0.0}
    monkeypatch.setattr(session_commands.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        session_commands.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + float(seconds)),
    )

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "2",
            "--max-wait-seconds",
            "50",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert slurm_poll_calls["count"] == 1
    assert clock["now"] == 2.0


def test_loop_pid_stall_triggers_early_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: False)

    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": "submitted\n<pid_number>999</pid_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(
        session_commands,
        "_query_pid_snapshot",
        lambda pid: session_commands._PidSnapshot(
            pid=pid, start_token="stalling-pid", cpu_seconds=10.0
        ),
    )

    clock = {"now": 0.0}
    monkeypatch.setattr(session_commands.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        session_commands.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + float(seconds)),
    )

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "1",
            "--max-wait-seconds",
            "100",
            "--pid-stall-seconds",
            "3",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert clock["now"] == 3.0


def test_loop_polling_status_heartbeat_every_10_minutes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: False)

    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": "submitted\n<pid_number>777</pid_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)
    monkeypatch.setattr(
        session_commands,
        "_query_pid_snapshot",
        lambda pid: session_commands._PidSnapshot(
            pid=pid, start_token="stable-pid", cpu_seconds=12.0
        ),
    )

    clock = {"now": 0.0}
    monkeypatch.setattr(session_commands.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        session_commands.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + float(seconds)),
    )

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "600",
            "--max-wait-seconds",
            "1250",
            "--pid-stall-seconds",
            "0",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    output = capsys.readouterr().out
    assert output.count("polling status @") == 2
    assert "waiting on: pid(s): 777" in output


def test_loop_slurm_unknown_state_triggers_early_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)
    monkeypatch.setattr(
        session_commands,
        "_query_slurm_job_state",
        lambda _job_id: "UNKNOWN",
    )

    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": "submitted\n<slurm_job_number>12345</slurm_job_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)

    clock = {"now": 0.0}
    monkeypatch.setattr(session_commands.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        session_commands.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + float(seconds)),
    )

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "1",
            "--max-wait-seconds",
            "20",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert clock["now"] == 2.0


def test_query_slurm_job_state_returns_unknown_for_squeue_invalid_job_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: "/usr/bin/squeue" if binary == "squeue" else None,
    )
    monkeypatch.setattr(
        session_commands,
        "_run_slurm_query",
        lambda command: subprocess.CompletedProcess(
            command,
            1,
            stdout="slurm_load_jobs error: Invalid job id specified\n",
            stderr="",
        ),
    )

    assert session_commands._query_slurm_job_state("12345") == "UNKNOWN"


def test_query_slurm_job_state_returns_unknown_for_sacct_error_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: "/usr/bin/sacct" if binary == "sacct" else None,
    )
    monkeypatch.setattr(
        session_commands,
        "_run_slurm_query",
        lambda command: subprocess.CompletedProcess(
            command,
            1,
            stdout="sacct: error: Invalid job id specified\n",
            stderr="",
        ),
    )

    assert session_commands._query_slurm_job_state("12345") == "UNKNOWN"


def test_query_slurm_job_state_uses_requested_sacct_job_row_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: "/usr/bin/sacct" if binary == "sacct" else None,
    )
    monkeypatch.setattr(
        session_commands,
        "_run_slurm_query",
        lambda command: subprocess.CompletedProcess(
            command,
            0,
            stdout="12345|RUNNING\n12345.batch|CANCELLED by 1000\n12345.extern|COMPLETED\n",
            stderr="",
        ),
    )

    assert session_commands._query_slurm_job_state("12345") == "RUNNING"


def test_query_slurm_job_state_prefers_active_over_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: "/usr/bin/sacct" if binary == "sacct" else None,
    )
    monkeypatch.setattr(
        session_commands,
        "_run_slurm_query",
        lambda command: subprocess.CompletedProcess(
            command,
            0,
            stdout="12345|COMPLETED\n12345|RUNNING\n",
            stderr="",
        ),
    )

    assert session_commands._query_slurm_job_state("12345") == "RUNNING"


def test_query_slurm_job_state_falls_back_to_squeue_when_sacct_has_no_exact_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: (
            "/usr/bin/sacct"
            if binary == "sacct"
            else "/usr/bin/squeue" if binary == "squeue" else None
        ),
    )

    def fake_run_slurm_query(command: list[str]):
        if command[0].endswith("sacct"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout="12345.batch|CANCELLED by 1000\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="RUNNING\n",
            stderr="",
        )

    monkeypatch.setattr(session_commands, "_run_slurm_query", fake_run_slurm_query)

    assert session_commands._query_slurm_job_state("12345") == "RUNNING"


def test_loop_slurm_query_parse_failure_triggers_early_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    monkeypatch.setattr(cli, "_ensure_exec_repo_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        cli, "resolve_scipkg_root", lambda: tmp_path / "scientific_packages"
    )
    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: {
            "package_id": "pkg-a",
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
    monkeypatch.setattr(cli, "_cleanup_exec_overlay_symlinks", lambda **_kwargs: None)
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)
    monkeypatch.setattr(
        session_commands.shutil,
        "which",
        lambda binary: "/usr/bin/squeue" if binary == "squeue" else None,
    )

    slurm_query_calls = {"count": 0}

    def fake_run_slurm_query(command: list[str]):
        slurm_query_calls["count"] += 1
        return subprocess.CompletedProcess(
            command,
            1,
            stdout="slurm_load_jobs error: Invalid job id specified\n",
            stderr="",
        )

    monkeypatch.setattr(session_commands, "_run_slurm_query", fake_run_slurm_query)

    run_calls: list[dict[str, object]] = []

    def fake_run_chat_turn(**kwargs):
        run_calls.append(kwargs)
        if len(run_calls) == 1:
            return {
                "assistant_text": "submitted\n<slurm_job_number>12345</slurm_job_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {"assistant_text": cli.LOOP_DONE_TOKEN, "return_code": 0, "stderr": ""}

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_chat_turn)

    clock = {"now": 0.0}
    monkeypatch.setattr(session_commands.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        session_commands.time,
        "sleep",
        lambda seconds: clock.__setitem__("now", clock["now"] + float(seconds)),
    )

    code = cli.main(
        [
            "loop",
            "--max-iterations",
            "2",
            "--wait-seconds",
            "1",
            "--max-wait-seconds",
            "20",
            "finish it",
        ]
    )
    assert code == 0
    assert len(run_calls) == 2
    assert slurm_query_calls["count"] >= 3
    assert clock["now"] == 2.0


def test_extract_loop_wait_seconds_returns_none_for_invalid_values() -> None:
    assert cli._extract_loop_wait_seconds("no token here") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>-1</wait_seconds>") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>abc</wait_seconds>") is None
    assert cli._extract_loop_wait_seconds("<wait_seconds>15</wait_seconds>") == 15.0


def test_extract_loop_pid_numbers_returns_unique_positive_integers() -> None:
    assert cli._extract_loop_pid_numbers("no token here") == []
    assert cli._extract_loop_pid_numbers("<pid_number>-1</pid_number>") == []
    assert cli._extract_loop_pid_numbers("<pid_number>abc</pid_number>") == []
    assert cli._extract_loop_pid_numbers("<pid_number>0</pid_number>") == []
    assert cli._extract_loop_pid_numbers(
        (
            "working\n"
            "<pid_number>15</pid_number>\n"
            "<pid_number>15</pid_number>\n"
            "<pid_number>42</pid_number>\n"
        )
    ) == [15, 42]


def test_extract_loop_slurm_job_numbers_returns_unique_values() -> None:
    assert cli._extract_loop_slurm_job_numbers("no token here") == []
    assert (
        cli._extract_loop_slurm_job_numbers("<slurm_job_number>abc</slurm_job_number>")
        == []
    )
    assert cli._extract_loop_slurm_job_numbers(
        (
            "working\n"
            "<slurm_job_number>123</slurm_job_number>\n"
            "<slurm_job_number>123</slurm_job_number>\n"
            "<slurm_job_number>456</slurm_job_number>\n"
        )
    ) == ["123", "456"]
    assert cli._extract_loop_slurm_job_numbers(
        (
            "<slurm_job_number>123_4</slurm_job_number>\n"
            "<slurm_job_number>123_4.batch</slurm_job_number>\n"
        )
    ) == ["123_4", "123_4.batch"]


def test_ensure_loop_memory_upgrades_legacy_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    memory_path = projects_dir / "memory.md"
    memory_path.write_text(
        (
            "# FermiLink Loop Memory\n\n"
            "- started_at_utc: 2026-01-01T00:00:00Z\n\n"
            "## Original request\nlegacy task\n\n"
            "## Plan\n- [ ] first step\n\n"
            "## Progress log\n- initialized\n"
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(repo_dir)
    result = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="legacy task",
        prompt_file=None,
        overwrite=False,
    )

    assert result == memory_path
    upgraded = memory_path.read_text(encoding="utf-8")
    assert "## Short-Term Memory (Operational)" in upgraded
    assert "### Plan" in upgraded
    assert "- [ ] first step" in upgraded
    assert "### Progress log" in upgraded
    assert "## Long-Term Memory (Persistent)" in upgraded
    assert "### Parameter source mapping" in upgraded
    assert "### Simulation uncertainty" in upgraded
    assert "### Suggested skills updates" in upgraded


def test_reset_loop_short_term_memory_preserves_long_term(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    memory_path = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="initial request",
        prompt_file=None,
        overwrite=False,
    )
    baseline = memory_path.read_text(encoding="utf-8")
    customized = baseline.replace(
        "- [ ] (fill in a small checklist plan)", "- [x] previous checklist item"
    ).replace("- initialized", "- previous progress entry")
    customized = customized.replace(
        "- (result_id | metric | value | conditions | evidence_path)\n",
        "- (result_id | metric | value | conditions | evidence_path)\n"
        "- result-001 | test_metric | 1.0 | baseline | artifacts/result.txt\n",
    )
    memory_path.write_text(customized, encoding="utf-8")

    workflow_commands._reset_loop_short_term_memory(
        repo_dir=repo_dir,
        user_prompt="next task",
        prompt_file=None,
        workflow_context_lines=["- workflow: reproduce"],
    )

    updated = memory_path.read_text(encoding="utf-8")
    assert "- [ ] (fill in a small checklist plan)" in updated
    assert "- initialized" in updated
    assert "- [x] previous checklist item" not in updated
    assert "- previous progress entry" not in updated
    assert (
        "- result-001 | test_metric | 1.0 | baseline | artifacts/result.txt" in updated
    )


def test_reset_loop_short_term_memory_upserts_workflow_context_when_memory_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    memory_path = cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="initial request",
        prompt_file="paper.md",
        overwrite=False,
    )
    baseline = memory_path.read_text(encoding="utf-8")
    assert "## Workflow context" not in baseline
    assert "## Original request\ninitial request" in baseline

    workflow_commands._reset_loop_short_term_memory(
        repo_dir=repo_dir,
        user_prompt="task prompt",
        prompt_file="projects/reproduce/run-001/prompts/task_001.md",
        workflow_context_lines=[
            "- workflow: reproduce",
            "- plan_json: projects/reproduce/run-001/plan.json",
            "- state_json: projects/reproduce/run-001/state.json",
        ],
    )

    updated = memory_path.read_text(encoding="utf-8")
    assert "## Workflow context" in updated
    assert "- workflow: reproduce" in updated
    assert "- plan_json: projects/reproduce/run-001/plan.json" in updated
    assert "- state_json: projects/reproduce/run-001/state.json" in updated
    assert "## Original request\ninitial request" in updated


def test_ensure_loop_memory_repeated_calls_do_not_duplicate_memory_sections(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="first",
        prompt_file=None,
        overwrite=False,
    )
    cli._ensure_loop_memory(
        repo_dir=repo_dir,
        user_prompt="second",
        prompt_file=None,
        overwrite=False,
    )

    memory = (repo_dir / "projects" / "memory.md").read_text(encoding="utf-8")
    assert memory.count("## Short-Term Memory (Operational)") == 1
    assert memory.count("## Long-Term Memory (Persistent)") == 1


def test_reset_loop_short_term_memory_canonicalizes_duplicate_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(repo_dir)

    memory_path = projects_dir / "memory.md"
    memory_path.write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "- schema_version: 1\n\n"
            "## Original request\nlegacy request\n\n"
            "## Short-Term Memory (Operational)\n\n"
            "### Plan\n"
            "- [x] stale checklist item\n\n"
            "### Progress log\n"
            "- stale progress entry\n\n"
            "## Long-Term Memory (Persistent)\n\n"
            "### Key results\n"
            "- keep-this-result | metric | 1.0 | baseline | artifacts/keep.txt\n\n"
            "## Short-Term Memory (Operational)\n\n"
            "### Plan\n"
            "- [ ] stale second checklist\n\n"
            "### Progress log\n"
            "- stale second progress\n\n"
            "## Long-Term Memory (Persistent)\n\n"
            "### Key results\n"
            "- (result_id | metric | value | conditions | evidence_path)\n"
        ),
        encoding="utf-8",
    )

    workflow_commands._reset_loop_short_term_memory(
        repo_dir=repo_dir,
        user_prompt="next run",
        prompt_file=None,
        workflow_context_lines=["- workflow: research"],
    )

    updated = memory_path.read_text(encoding="utf-8")
    assert updated.count("## Short-Term Memory (Operational)") == 1
    assert updated.count("## Long-Term Memory (Persistent)") == 1
    assert "- [ ] (fill in a small checklist plan)" in updated
    assert "- initialized" in updated
    assert "- [x] stale checklist item" not in updated
    assert "- stale progress entry" not in updated
    assert (
        "- keep-this-result | metric | 1.0 | baseline | artifacts/keep.txt" in updated
    )
