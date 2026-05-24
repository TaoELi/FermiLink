from __future__ import annotations

import json
from pathlib import Path

from fermilink import cli
from fermilink.drvloop import artifacts
from fermilink.drvloop.instructions import materialize_drvloop_instructions
from fermilink.drvloop.main import DrvloopConfig, run_drvloop
from fermilink.drvloop import main as drvloop_main
from fermilink.drvloop.memory import ensure_drvloop_memory
from fermilink.drvloop.prompts import DRVLOOP_DONE_TOKEN, load_drvloop_guide


def test_materialize_drvloop_instructions_copies_guide_and_aliases(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    instruction_files = materialize_drvloop_instructions(repo)

    agents_path = repo / "AGENTS.md"
    assert instruction_files.agents_path == agents_path
    assert agents_path.read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
        if alias_path.is_symlink():
            assert alias_path.resolve() == agents_path.resolve()
        else:
            assert alias_path.read_text(encoding="utf-8") == load_drvloop_guide()


def test_materialize_drvloop_instructions_preserves_differing_agents_backup(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("custom local guide\n", encoding="utf-8")

    materialize_drvloop_instructions(repo)

    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    backup = repo / "AGENTS.md.pre-drvloop-backup"
    assert backup.read_text(encoding="utf-8") == "custom local guide\n"


def test_drvloop_memory_template_is_compact_major_step_memory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive the coupled oscillator response",
        prompt_file=None,
    )

    memory_text = memory.read_text(encoding="utf-8")
    assert "### Major done" in memory_text
    assert "### Major needed" in memory_text
    assert "### Major conclusions" in memory_text
    assert "Measurement data inventory" not in memory_text
    assert "Experiment history" not in memory_text
    assert "Simulation history" not in memory_text


def test_drvloop_runs_without_git_init_and_uses_minimal_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    skill_dir = repo / "skills" / "symbolic-derivation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Symbolic derivation\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive the adiabatic elimination equations",
            max_iterations=1,
        )
    )

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    memory = repo / "projects" / "memory.md"
    assert memory.is_file()
    memory_text = memory.read_text(encoding="utf-8")
    assert "derive the adiabatic elimination equations" in memory_text
    assert "Measurement data inventory" not in memory_text
    prompt = str(captured.get("prompt") or "")
    assert "FermiLink drvloop mode: derivation work." in prompt
    assert "Read `AGENTS.md` and `projects/memory.md`" in prompt
    assert "skills/symbolic-derivation/SKILL.md" in prompt
    assert "<pid_number>" not in prompt
    assert "<wait_seconds>" not in prompt
    assert "# FermiLink Drvloop Guide" not in prompt


def test_drvloop_artifact_scan_records_state_without_expanding_memory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive",
        prompt_file=None,
    )

    first_changes = artifacts.record_artifact_changes(repo, memory)
    assert first_changes == []
    assert artifacts.state_path_for(repo) == repo / "fermilink-drvloop" / "state.json"
    assert artifacts.state_path_for(repo).is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()

    run_dir = repo / "projects" / "2026-05-24-derivation"
    run_dir.mkdir(parents=True)
    derivation_file = run_dir / "route-a.md"
    derivation_file.write_text("step 1\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)

    assert [item["path"] for item in changes] == [
        "projects/2026-05-24-derivation/route-a.md"
    ]
    memory_text = memory.read_text(encoding="utf-8")
    assert "projects/2026-05-24-derivation/route-a.md" not in memory_text
    assert "Measurement data inventory" not in memory_text


def test_drvloop_artifact_state_reads_legacy_hidden_state_then_writes_visible_state(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive",
        prompt_file=None,
    )
    run_dir = repo / "projects" / "2026-05-24-derivation"
    run_dir.mkdir(parents=True)
    old_file = run_dir / "old.md"
    old_file.write_text("old\n", encoding="utf-8")
    old_record = artifacts.snapshot_project_artifacts(repo)[
        "projects/2026-05-24-derivation/old.md"
    ]
    legacy_state = repo / ".fermilink-drvloop" / "state.json"
    legacy_state.parent.mkdir(parents=True)
    legacy_payload = {
        "version": 1,
        "last_scan_at_utc": "2026-05-24T00:00:00Z",
        "known_artifacts": {
            "projects/2026-05-24-derivation/old.md": old_record,
        },
    }
    legacy_state.write_text(json.dumps(legacy_payload), encoding="utf-8")

    new_file = run_dir / "new.md"
    new_file.write_text("new\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)

    assert [item["path"] for item in changes] == [
        "projects/2026-05-24-derivation/new.md"
    ]
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert legacy_state.is_file()


def test_drvloop_runs_next_turn_immediately_until_done(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    provider_calls: list[dict[str, object]] = []

    def fake_provider_turn(**kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return {
                "assistant_text": "stored route A and will continue\n",
                "return_code": 0,
                "stderr": "",
            }
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive route A then check route B",
            max_iterations=2,
        )
    )

    assert code == 0
    assert len(provider_calls) == 2


def test_debug_parser_does_not_support_skill_folder() -> None:
    parser = drvloop_main._build_arg_parser()
    args = parser.parse_args(["goal.md", "--max-iterations", "1"])
    assert args.prompt == ["goal.md"]
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings
    assert "--wait-seconds" not in option_strings
    assert "--max-wait-seconds" not in option_strings


def test_cli_drvloop_wires_to_runner_without_git_or_skill_folder(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    goal = repo / "goal.md"
    goal.write_text("derive a compact response equation", encoding="utf-8")
    monkeypatch.chdir(repo)

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = cli.main(["drvloop", "--max-iterations", "1", "goal.md"])

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    assert (repo / "projects" / "memory.md").is_file()
    assert "derive a compact response equation" in str(captured.get("prompt") or "")

    parser = cli._build_parser()
    args = parser.parse_args(["drvloop", "goal.md"])
    assert args.func is cli._cmd_drvloop
    option_strings = {
        option
        for action in parser._subparsers._group_actions[0].choices["drvloop"]._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings
    assert "--init-git" not in option_strings
    assert "--package" not in option_strings
    assert "--wait-seconds" not in option_strings
    assert "--max-wait-seconds" not in option_strings


def test_cli_drvloop_validation_error_uses_standard_cli_exit(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    monkeypatch.chdir(repo)

    code = cli.main(["drvloop", "--max-iterations", "0", "derive"])

    assert code == 2
    assert "max_iterations must be >= 1." in capsys.readouterr().err
