from __future__ import annotations

from pathlib import Path
import runpy

import setuptools


def _load_setup_namespace(monkeypatch) -> dict[str, object]:
    repo_root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(setuptools, "setup", lambda *args, **kwargs: None)
    return runpy.run_path(str(repo_root / "setup.py"))


def test_copy_workspace_payload_maps_agents_to_init_template(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = _load_setup_namespace(monkeypatch)
    copy_workspace_payload = namespace["_copy_workspace_payload"]

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "AGENTS.md").write_text("dev agents\n", encoding="utf-8")
    (repo_root / "README.md").write_text("readme\n", encoding="utf-8")

    init_template_agents = (
        repo_root / "src" / "fermilink" / "init_template" / "AGENTS.md"
    )
    init_template_agents.parent.mkdir(parents=True, exist_ok=True)
    init_template_agents.write_text("init template agents\n", encoding="utf-8")

    payload_root = tmp_path / "payload"
    copy_workspace_payload(repo_root, payload_root)

    assert (payload_root / "AGENTS.md").read_text(encoding="utf-8") == (
        "init template agents\n"
    )


def test_copy_workspace_payload_agents_falls_back_to_repo_root(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = _load_setup_namespace(monkeypatch)
    copy_workspace_payload = namespace["_copy_workspace_payload"]

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "AGENTS.md").write_text("dev agents\n", encoding="utf-8")
    (repo_root / "README.md").write_text("readme\n", encoding="utf-8")

    payload_root = tmp_path / "payload"
    copy_workspace_payload(repo_root, payload_root)

    assert (payload_root / "AGENTS.md").read_text(encoding="utf-8") == "dev agents\n"


def test_copy_workspace_payload_includes_skills_directory(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = _load_setup_namespace(monkeypatch)
    copy_workspace_payload = namespace["_copy_workspace_payload"]

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "AGENTS.md").write_text("dev agents\n", encoding="utf-8")
    (repo_root / "README.md").write_text("readme\n", encoding="utf-8")
    skills_dir = repo_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "README.md").write_text("keep\n", encoding="utf-8")

    payload_root = tmp_path / "payload"
    copy_workspace_payload(repo_root, payload_root)

    assert (payload_root / "skills").is_dir()
    assert (payload_root / "skills" / "README.md").read_text(encoding="utf-8") == "keep\n"


def test_copy_workspace_payload_materializes_empty_skills_directory(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = _load_setup_namespace(monkeypatch)
    copy_workspace_payload = namespace["_copy_workspace_payload"]

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "AGENTS.md").write_text("dev agents\n", encoding="utf-8")
    (repo_root / "README.md").write_text("readme\n", encoding="utf-8")
    (repo_root / "skills").mkdir(parents=True, exist_ok=True)

    payload_root = tmp_path / "payload"
    copy_workspace_payload(repo_root, payload_root)

    assert (payload_root / "skills").is_dir()
    sentinel = payload_root / "skills" / "_fermilink_keep"
    assert sentinel.is_file()
    assert "Keep otherwise-empty payload directory" in sentinel.read_text(
        encoding="utf-8"
    )


def test_copy_workspace_payload_excludes_hidden_paths(
    monkeypatch, tmp_path: Path
) -> None:
    namespace = _load_setup_namespace(monkeypatch)
    copy_workspace_payload = namespace["_copy_workspace_payload"]

    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    (repo_root / "AGENTS.md").write_text("dev agents\n", encoding="utf-8")
    (repo_root / "README.md").write_text("readme\n", encoding="utf-8")
    (repo_root / ".gitignore").write_text("dist/\n", encoding="utf-8")
    hidden_workflow = repo_root / ".github" / "workflows"
    hidden_workflow.mkdir(parents=True, exist_ok=True)
    (hidden_workflow / "ci.yml").write_text("name: ci\n", encoding="utf-8")
    skills_dir = repo_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / ".private").write_text("secret\n", encoding="utf-8")
    (skills_dir / "public.md").write_text("ok\n", encoding="utf-8")

    payload_root = tmp_path / "payload"
    copy_workspace_payload(repo_root, payload_root)

    assert not (payload_root / ".gitignore").exists()
    assert not (payload_root / ".github").exists()
    assert not (payload_root / "skills" / ".private").exists()
    assert (payload_root / "skills" / "public.md").is_file()
