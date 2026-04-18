from __future__ import annotations

import json
from pathlib import Path

from fermilink import cli


def _parse_stdout_json(capsys) -> dict[str, object]:
    out = capsys.readouterr().out.strip()
    assert out
    return json.loads(out)


def test_agent_shows_defaults_when_unconfigured(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    code = cli.main(["agent", "--json"])
    assert code == 0

    payload = _parse_stdout_json(capsys)
    assert payload["provider"] == "codex"
    assert payload["sandbox_policy"] == "enforce"
    assert payload["sandbox_mode"] == "workspace-write"
    assert payload["model"] is None
    assert payload["reasoning_effort"] is None


def test_agent_updates_provider_and_sandbox_policy(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    code = cli.main(["agent", "gemini", "--bypass-sandbox", "--json"])
    assert code == 0
    payload = _parse_stdout_json(capsys)
    assert payload["provider"] == "gemini"
    assert payload["sandbox_policy"] == "bypass"

    code = cli.main(["agent", "--json"])
    assert code == 0
    persisted = _parse_stdout_json(capsys)
    assert persisted["provider"] == "gemini"
    assert persisted["sandbox_policy"] == "bypass"


def test_agent_accepts_deepseek_provider(monkeypatch, tmp_path: Path, capsys) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    code = cli.main(["agent", "deepseek", "--json"])
    assert code == 0
    payload = _parse_stdout_json(capsys)
    assert payload["provider"] == "deepseek"


def test_agent_prints_bypass_hint_when_setting_provider(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    assert cli.main(["agent", "claude"]) == 0
    out = capsys.readouterr().out
    assert "Provider set to claude." in out
    assert (
        "Trusted local repo? Run `fermilink agent claude --bypass-sandbox`."
        in out
    )


def test_agent_enables_sandbox_without_changing_mode(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    assert cli.main(["agent", "--bypass-sandbox"]) == 0
    capsys.readouterr()

    assert cli.main(["agent", "--sandbox", "--json"]) == 0
    payload = _parse_stdout_json(capsys)
    assert payload["sandbox_policy"] == "enforce"
    assert payload["sandbox_mode"] == "workspace-write"


def test_agent_sets_and_clears_model_override(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    assert cli.main(["agent", "--model", "gpt-5.3-codex", "--json"]) == 0
    payload = _parse_stdout_json(capsys)
    assert payload["model"] == "gpt-5.3-codex"

    assert cli.main(["agent", "--clear-model", "--json"]) == 0
    cleared = _parse_stdout_json(capsys)
    assert cleared["model"] is None


def test_agent_sets_and_clears_reasoning_effort(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    home = tmp_path / "fermilink-home"
    monkeypatch.setenv("FERMILINK_HOME", str(home))

    assert cli.main(["agent", "--reasoning-effort", "high", "--json"]) == 0
    payload = _parse_stdout_json(capsys)
    assert payload["reasoning_effort"] == "high"

    assert cli.main(["agent", "--clear-reasoning-effort", "--json"]) == 0
    cleared = _parse_stdout_json(capsys)
    assert cleared["reasoning_effort"] is None
