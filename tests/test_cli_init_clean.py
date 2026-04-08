from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from fermilink import cli
from fermilink.cli.commands import workspace as workspace_commands

INIT_TEMPLATE_AGENTS_TEXT = "init template agents\n"
LEGACY_TOP_LEVEL_AGENTS_TEXT = "payload top-level agents\n"


def _symlink_supported() -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source = base / "source.txt"
        source.write_text("x", encoding="utf-8")
        link = base / "link.txt"
        try:
            link.symlink_to(source.name)
        except (OSError, NotImplementedError):
            return False
        return link.is_symlink()


pytestmark = pytest.mark.skipif(
    not _symlink_supported(),
    reason="Symlink support is not available in this environment.",
)


def _assert_points_to(link_path: Path, expected_source: Path) -> None:
    assert link_path.is_symlink()
    resolved = (link_path.parent / link_path.readlink()).resolve()
    assert resolved == expected_source.resolve()


@pytest.fixture
def payload_root(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    payload.mkdir(parents=True, exist_ok=True)

    (payload / "README.md").write_text("payload readme\n", encoding="utf-8")
    (payload / "AGENTS.md").write_text(
        LEGACY_TOP_LEVEL_AGENTS_TEXT,
        encoding="utf-8",
    )
    (payload / "pyproject.toml").write_text(
        "[project]\nname='payload'\n", encoding="utf-8"
    )

    src_dir = payload / "src" / "fermilink"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    init_template_dir = src_dir / "init_template"
    init_template_dir.mkdir(parents=True, exist_ok=True)
    (init_template_dir / "AGENTS.md").write_text(
        INIT_TEMPLATE_AGENTS_TEXT,
        encoding="utf-8",
    )

    tests_dir = payload / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_placeholder.py").write_text(
        "def test_placeholder():\n    pass\n", encoding="utf-8"
    )

    scripts_dir = payload / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "run.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")

    skills_dir = payload / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "README.md").write_text("skills\n", encoding="utf-8")

    hidden_dir = payload / ".github" / "workflows"
    hidden_dir.mkdir(parents=True, exist_ok=True)
    (hidden_dir / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    (payload / ".env").write_text("SECRET=1\n", encoding="utf-8")

    return payload


def test_cli_init_and_clean_manage_workspace_links(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    assert cli.main(["init", str(workdir)]) == 0
    _assert_points_to(workdir / "README.md", payload_root / "README.md")
    agents_path = workdir / "AGENTS.md"
    assert agents_path.is_file()
    assert not agents_path.is_symlink()
    assert agents_path.read_text(encoding="utf-8") == INIT_TEMPLATE_AGENTS_TEXT
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "src", payload_root / "src")
    _assert_points_to(workdir / "tests", payload_root / "tests")
    _assert_points_to(workdir / "scripts", payload_root / "scripts")
    _assert_points_to(workdir / "skills", payload_root / "skills")
    assert not (workdir / ".github").exists()
    assert not (workdir / ".env").exists()

    assert cli.main(["clean", str(workdir)]) == 0
    assert not (workdir / "README.md").exists()
    assert not (workdir / "AGENTS.md").exists()
    assert not (workdir / "src").exists()
    assert not (workdir / "tests").exists()
    assert not (workdir / "scripts").exists()
    assert not (workdir / "skills").exists()
    assert not (workdir / "CLAUDE.md").exists()
    assert not (workdir / "GEMINI.md").exists()


def test_cli_init_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    (workdir / "README.md").write_text("local readme\n", encoding="utf-8")

    assert cli.main(["init", str(workdir)]) == 2
    assert "Conflict at" in capsys.readouterr().err

    assert cli.main(["init", str(workdir), "--force"]) == 0
    _assert_points_to(workdir / "README.md", payload_root / "README.md")


def test_cli_init_agents_alias_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    (workdir / "CLAUDE.md").write_text("local alias file\n", encoding="utf-8")

    assert cli.main(["init", str(workdir)]) == 2
    assert "Conflict at" in capsys.readouterr().err

    assert cli.main(["init", str(workdir), "--force"]) == 0
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")


def test_cli_clean_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    assert cli.main(["init", str(workdir)]) == 0
    (workdir / "README.md").unlink()
    (workdir / "README.md").write_text("edited", encoding="utf-8")

    assert cli.main(["clean", str(workdir)]) == 2
    assert "expected symlink" in capsys.readouterr().err

    assert cli.main(["clean", str(workdir), "--force"]) == 0
    assert not (workdir / "README.md").exists()


def test_cli_clean_agents_copy_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    assert cli.main(["init", str(workdir)]) == 0
    (workdir / "AGENTS.md").write_text("edited\n", encoding="utf-8")

    assert cli.main(["clean", str(workdir)]) == 2
    assert "expected managed AGENTS.md file content" in capsys.readouterr().err

    assert cli.main(["clean", str(workdir), "--force"]) == 0
    assert not (workdir / "AGENTS.md").exists()


def test_cli_clean_does_not_remove_hidden_local_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    assert cli.main(["init", str(workdir)]) == 0
    (workdir / ".env").write_text("LOCAL=1\n", encoding="utf-8")
    (workdir / ".github").mkdir(parents=True, exist_ok=True)
    (workdir / ".github" / "local.txt").write_text("keep\n", encoding="utf-8")

    assert cli.main(["clean", str(workdir)]) == 0
    assert (workdir / ".env").is_file()
    assert (workdir / ".github").is_dir()


def test_standalone_init_clean_entrypoints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        workspace_commands, "_resolve_payload_root", lambda: payload_root
    )

    assert workspace_commands.fermilink_init_main([str(workdir)]) == 0
    _assert_points_to(workdir / "src", payload_root / "src")
    _assert_points_to(workdir / "skills", payload_root / "skills")
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")

    assert workspace_commands.fermilink_clean_main([str(workdir)]) == 0
    assert not (workdir / "src").exists()
    assert not (workdir / "skills").exists()
    assert not (workdir / "CLAUDE.md").exists()
    assert not (workdir / "GEMINI.md").exists()
