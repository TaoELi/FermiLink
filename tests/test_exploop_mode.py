from __future__ import annotations

import builtins
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from fermilink import cli
from fermilink.exploop import artifacts
from fermilink.exploop.instructions import materialize_exploop_instructions
from fermilink.exploop.main import ExploopConfig, run_exploop
from fermilink.exploop import main as exploop_main
from fermilink.exploop.memory import ensure_exploop_memory
from fermilink.exploop.pid import _windows_pid_alive
from fermilink.exploop.prompts import EXPLOOP_DONE_TOKEN, load_exploop_guide


def test_materialize_exploop_instructions_copies_guide_and_aliases(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    instruction_files = materialize_exploop_instructions(repo)

    agents_path = repo / "AGENTS.md"
    assert instruction_files.agents_path == agents_path
    assert agents_path.read_text(encoding="utf-8") == load_exploop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
        if alias_path.is_symlink():
            assert alias_path.resolve() == agents_path.resolve()
        else:
            assert alias_path.read_text(encoding="utf-8") == load_exploop_guide()


def test_materialize_exploop_instructions_preserves_differing_agents_backup(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("custom local guide\n", encoding="utf-8")

    materialize_exploop_instructions(repo)

    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_exploop_guide()
    backup = repo / "AGENTS.md.pre-exploop-backup"
    assert backup.read_text(encoding="utf-8") == "custom local guide\n"


def test_exploop_guide_pid_examples_emit_parseable_tags() -> None:
    guide = load_exploop_guide()

    assert 'Write-Output "<pid_number>$($p.Id)</pid_number>"' in guide
    assert 'print(f"<pid_number>{proc.pid}</pid_number>")' in guide
    assert "\n$p.Id\n" not in guide
    assert "print(proc.pid)" not in guide
    assert "final detached measurement process" in guide
    assert "RedirectStandardInput" in guide
    assert "RedirectStandardOutput" in guide
    assert "RedirectStandardError" in guide
    assert "Do not call `Wait-Process`, `communicate()`" in guide


def test_exploop_memory_template_recommends_grouped_measurement_inventory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    memory = ensure_exploop_memory(
        repo_dir=repo,
        user_prompt="measure",
        prompt_file=None,
    )

    memory_text = memory.read_text(encoding="utf-8")
    assert "### Measurement data inventory" in memory_text
    assert "combine them into one grouped entry by pattern/count/location" in memory_text
    assert "so this memory file stays compact" in memory_text


def test_exploop_runs_without_git_init_and_discovers_local_skills(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    skill_dir = repo / "skills" / "mos2-transport"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# MoS2 transport\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        return {
            "assistant_text": f"done\n{EXPLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(exploop_main, "_run_provider_turn", fake_provider_turn)

    code = run_exploop(
        ExploopConfig(
            repo_dir=repo,
            user_prompt="measure a conservative TG scan",
            max_iterations=1,
        )
    )

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-exploop" / "state.json").is_file()
    assert not (repo / ".fermilink-exploop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_exploop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    memory = repo / "projects" / "memory.md"
    assert memory.is_file()
    assert "measure a conservative TG scan" in memory.read_text(encoding="utf-8")
    prompt = str(captured.get("prompt") or "")
    assert "skills/mos2-transport/SKILL.md" in prompt
    assert "Read the local `AGENTS.md` guide" in prompt
    assert "Packaged exploop guide:" not in prompt
    assert "# FermiLink Exploop Guide" not in prompt
    assert "There is no `--skill-folder` option" not in prompt


def test_artifact_scan_baselines_then_records_new_project_data(tmp_path: Path) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_exploop_memory(
        repo_dir=repo,
        user_prompt="measure",
        prompt_file=None,
    )

    first_changes = artifacts.record_artifact_changes(repo, memory)
    assert first_changes == []
    assert artifacts.state_path_for(repo) == repo / "fermilink-exploop" / "state.json"
    assert artifacts.state_path_for(repo).is_file()
    assert not (repo / ".fermilink-exploop" / "state.json").exists()

    run_dir = repo / "projects" / "2026-05-12-mos2"
    run_dir.mkdir(parents=True)
    data_file = run_dir / "scantg_bg0V_T0.01K_0Tesla.txt"
    data_file.write_text("0 1 2\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)
    assert [item["path"] for item in changes] == [
        "projects/2026-05-12-mos2/scantg_bg0V_T0.01K_0Tesla.txt"
    ]
    memory_text = memory.read_text(encoding="utf-8")
    assert "projects/2026-05-12-mos2/scantg_bg0V_T0.01K_0Tesla.txt" in memory_text
    assert "observed 1 new/modified measurement artifact" in memory_text

    assert artifacts.record_artifact_changes(repo, memory) == []


def test_artifact_state_reads_legacy_hidden_state_then_writes_visible_state(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_exploop_memory(
        repo_dir=repo,
        user_prompt="measure",
        prompt_file=None,
    )
    run_dir = repo / "projects" / "2026-05-12-mos2"
    run_dir.mkdir(parents=True)
    old_file = run_dir / "old.dat"
    old_file.write_text("old\n", encoding="utf-8")
    old_record = artifacts.snapshot_project_artifacts(repo)[
        "projects/2026-05-12-mos2/old.dat"
    ]
    legacy_state = repo / ".fermilink-exploop" / "state.json"
    legacy_state.parent.mkdir(parents=True)
    legacy_payload = {
        "version": 1,
        "last_scan_at_utc": "2026-05-12T00:00:00Z",
        "known_artifacts": {
            "projects/2026-05-12-mos2/old.dat": old_record,
        },
    }
    legacy_state.write_text(json.dumps(legacy_payload), encoding="utf-8")

    new_file = run_dir / "new.dat"
    new_file.write_text("new\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)

    assert [item["path"] for item in changes] == ["projects/2026-05-12-mos2/new.dat"]
    assert (repo / "fermilink-exploop" / "state.json").is_file()
    assert legacy_state.is_file()


def test_windows_pid_alive_parses_tasklist_csv() -> None:
    def alive_runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='"python.exe","12345","Console","1","10,000 K"\n',
        )

    def missing_runner(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="INFO: No tasks are running which match the specified criteria.\n",
        )

    assert _windows_pid_alive(12345, runner=alive_runner)
    assert not _windows_pid_alive(12345, runner=missing_runner)


def test_windows_pid_alive_handles_tasklist_failure() -> None:
    def failing_runner(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(["tasklist"], timeout=8)

    assert not _windows_pid_alive(12345, runner=failing_runner)


def test_exploop_waits_for_emitted_pid_then_runs_next_turn(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    provider_calls: list[dict[str, object]] = []

    def fake_provider_turn(**kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return {
                "assistant_text": "<pid_number>12345</pid_number>\n",
                "return_code": 0,
                "stderr": "",
            }
        return {
            "assistant_text": f"done\n{EXPLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    pid_alive_results = [True, False]
    checked_pids: list[int] = []

    def fake_is_pid_alive(pid: int) -> bool:
        checked_pids.append(pid)
        return pid_alive_results.pop(0)

    monkeypatch.setattr(exploop_main, "_run_provider_turn", fake_provider_turn)
    monkeypatch.setattr(exploop_main, "is_pid_alive", fake_is_pid_alive)
    monkeypatch.setattr(exploop_main.time, "sleep", lambda _seconds: None)

    code = run_exploop(
        ExploopConfig(
            repo_dir=repo,
            user_prompt="run a measurement",
            max_iterations=2,
            wait_seconds=0.1,
            max_wait_seconds=30,
        )
    )

    assert code == 0
    assert checked_pids == [12345, 12345]
    assert len(provider_calls) == 2


def test_exploop_polls_pid_even_on_final_iteration(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    def fake_provider_turn(**_kwargs):
        return {
            "assistant_text": "<pid_number>12345</pid_number>\n",
            "return_code": 0,
            "stderr": "",
        }

    pid_alive_results = [True, False]
    checked_pids: list[int] = []

    def fake_is_pid_alive(pid: int) -> bool:
        checked_pids.append(pid)
        return pid_alive_results.pop(0)

    monkeypatch.setattr(exploop_main, "_run_provider_turn", fake_provider_turn)
    monkeypatch.setattr(exploop_main, "is_pid_alive", fake_is_pid_alive)
    monkeypatch.setattr(exploop_main.time, "sleep", lambda _seconds: None)

    code = run_exploop(
        ExploopConfig(
            repo_dir=repo,
            user_prompt="run one measurement",
            max_iterations=1,
            wait_seconds=0.1,
            max_wait_seconds=30,
        )
    )

    assert code == 1
    assert checked_pids == [12345, 12345]


def test_exploop_stops_before_next_turn_when_pid_reaches_max_wait(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    provider_calls: list[dict[str, object]] = []

    def fake_provider_turn(**kwargs):
        provider_calls.append(kwargs)
        return {
            "assistant_text": "<pid_number>12345</pid_number>\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(exploop_main, "_run_provider_turn", fake_provider_turn)
    monkeypatch.setattr(exploop_main, "is_pid_alive", lambda _pid: True)

    code = run_exploop(
        ExploopConfig(
            repo_dir=repo,
            user_prompt="run a long measurement",
            max_iterations=2,
            wait_seconds=0.1,
            max_wait_seconds=0,
        )
    )

    memory_text = (repo / "projects" / "memory.md").read_text(encoding="utf-8")
    assert code == 1
    assert len(provider_calls) == 1
    assert "still-running PID(s): 12345" in memory_text


def test_pid_polling_logs_start_and_minute_progress(monkeypatch, capsys) -> None:
    pid_alive_results = [True, True, False]

    def fake_is_pid_alive(pid: int) -> bool:
        assert pid == 12345
        return pid_alive_results.pop(0)

    monotonic_values = iter([0.0, 0.0, 61.0, 61.0, 61.5])

    monkeypatch.setattr(exploop_main, "is_pid_alive", fake_is_pid_alive)
    monkeypatch.setattr(exploop_main.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(exploop_main.time, "sleep", lambda _seconds: None)

    still_alive = exploop_main._wait_for_pids(
        [12345],
        poll_seconds=0.1,
        max_wait_seconds=6000,
    )

    captured = capsys.readouterr()
    output = captured.out
    assert still_alive == []
    assert captured.err == ""
    assert (
        "measurement running; polling PID(s) every 0.1s and showing status every 60s: 12345"
        in output
    )
    assert "still waiting for measurement PID(s) after 61.0s: 12345" in output
    assert "measurement PID polling complete after 61.5s" in output


def test_pid_polling_warns_when_tagged_pids_are_not_alive(monkeypatch, capsys) -> None:
    monkeypatch.setattr(exploop_main, "is_pid_alive", lambda _pid: False)

    still_alive = exploop_main._wait_for_pids(
        [12345],
        poll_seconds=0.1,
        max_wait_seconds=6000,
    )

    captured = capsys.readouterr()
    assert still_alive == []
    assert captured.err == ""
    assert "PID(s) were already finished or not found" in captured.out
    assert "detached measurement process, not a wrapper" in captured.out
    assert "PID(s): 12345" in captured.out


def test_pid_polling_sleep_wakes_for_minute_progress(monkeypatch) -> None:
    pid_alive_results = [True, True, False]
    monotonic_values = iter([0.0, 0.0, 60.0, 60.0, 120.0])
    slept: list[float] = []

    monkeypatch.setattr(
        exploop_main,
        "is_pid_alive",
        lambda _pid: pid_alive_results.pop(0),
    )
    monkeypatch.setattr(exploop_main.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        exploop_main.time,
        "sleep",
        lambda seconds: slept.append(seconds),
    )

    still_alive = exploop_main._wait_for_pids(
        [12345],
        poll_seconds=120.0,
        max_wait_seconds=600.0,
    )

    assert still_alive == []
    assert slept[0] == 60.0


def test_exploop_tagged_status_output_flushes(monkeypatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_print(*args, **kwargs) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(builtins, "print", fake_print)

    exploop_main._print_tagged("exploop", "status update")

    assert calls == [(("[exploop] status update",), {"flush": True})]


def test_debug_parser_does_not_support_skill_folder() -> None:
    parser = exploop_main._build_arg_parser()
    args = parser.parse_args(["goal.md", "--max-iterations", "1"])
    assert args.prompt == ["goal.md"]
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings


def test_cli_exploop_wires_to_runner_without_git_or_skill_folder(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    goal = repo / "goal.md"
    goal.write_text("measure a cautious gate scan", encoding="utf-8")
    monkeypatch.chdir(repo)

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        return {
            "assistant_text": f"done\n{EXPLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(exploop_main, "_run_provider_turn", fake_provider_turn)

    code = cli.main(["exploop", "--max-iterations", "1", "goal.md"])

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-exploop" / "state.json").is_file()
    assert not (repo / ".fermilink-exploop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_exploop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    assert (repo / "projects" / "memory.md").is_file()
    assert "measure a cautious gate scan" in str(captured.get("prompt") or "")

    parser = cli._build_parser()
    args = parser.parse_args(["exploop", "goal.md"])
    assert args.func is cli._cmd_exploop
    option_strings = {
        option
        for action in parser._subparsers._group_actions[0].choices["exploop"]._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings
    assert "--init-git" not in option_strings
    assert "--package" not in option_strings


def test_cli_exploop_validation_error_uses_standard_cli_exit(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    monkeypatch.chdir(repo)

    code = cli.main(["exploop", "--max-iterations", "0", "measure"])

    assert code == 2
    assert "max_iterations must be >= 1." in capsys.readouterr().err
