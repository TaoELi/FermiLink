from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pytest

from fermilink import cli
from fermilink.cli.commands import workspace as workspace_commands
from fermilink.exploop.prompts import load_exploop_guide
from fermilink.packages.package_registry import install_from_local_path
from fermilink.runner import app as runner_app
from fermilink.runner import scientific_packages as runner_scipkg
from fermilink.workspace import payload as workspace_payload

INIT_TEMPLATE_AGENTS_TEXT = "init template agents\n"
LEGACY_TOP_LEVEL_AGENTS_TEXT = "payload top-level agents\n"
SOFTWARE_TEMPLATE_AGENTS_TEXT = "software template agents\n"
PACKAGE_SOURCE_AGENTS_TEXT = "package source agents\n"


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


def _configure_software_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Path:
    software_dir = tmp_path / "software"
    software_dir.mkdir(parents=True, exist_ok=True)
    (software_dir / "AGENTS.md").write_text(
        SOFTWARE_TEMPLATE_AGENTS_TEXT,
        encoding="utf-8",
    )
    monkeypatch.setattr(runner_app, "_resolve_source_dir", lambda: software_dir)
    monkeypatch.setattr(
        runner_app,
        "_resolve_template_agents_path",
        lambda _source_dir: software_dir / "AGENTS.md",
    )
    return software_dir


def _install_package_fixture(
    tmp_path: Path,
    *,
    package_id: str,
    workflow_type: str = "simulation",
) -> tuple[Path, Path]:
    scipkg_root = tmp_path / "scientific_packages"
    package_root = tmp_path / f"{package_id}-src"
    package_root.mkdir(parents=True, exist_ok=True)

    (package_root / "README.md").write_text("package readme\n", encoding="utf-8")
    (package_root / "AGENTS.md").write_text(
        PACKAGE_SOURCE_AGENTS_TEXT,
        encoding="utf-8",
    )
    src_dir = package_root / "src" / package_id
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "__init__.py").write_text("__all__ = []\n", encoding="utf-8")
    skills_dir = package_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    (skills_dir / "README.md").write_text("package skills\n", encoding="utf-8")
    public_dir = package_root / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "index.html").write_text("<html></html>\n", encoding="utf-8")

    install_from_local_path(
        scipkg_root,
        package_id,
        local_path=package_root,
        activate=True,
        workflow_type=workflow_type,
    )
    return scipkg_root, package_root


def test_cli_init_and_clean_manage_workspace_links(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

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
    skills_path = workdir / "skills"
    assert skills_path.is_dir()
    assert not skills_path.is_symlink()
    assert (skills_path / "README.md").read_text(encoding="utf-8") == "skills\n"
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
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

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
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

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
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

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
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

    assert cli.main(["init", str(workdir)]) == 0
    (workdir / "AGENTS.md").write_text("edited\n", encoding="utf-8")

    assert cli.main(["clean", str(workdir)]) == 2
    assert "expected managed AGENTS.md file content" in capsys.readouterr().err

    assert cli.main(["clean", str(workdir), "--force"]) == 0
    assert not (workdir / "AGENTS.md").exists()


def test_cli_clean_skills_copy_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

    assert cli.main(["init", str(workdir)]) == 0
    (workdir / "skills" / "README.md").write_text("edited\n", encoding="utf-8")

    assert cli.main(["clean", str(workdir)]) == 2
    assert "expected managed copied directory content" in capsys.readouterr().err

    assert cli.main(["clean", str(workdir), "--force"]) == 0
    assert not (workdir / "skills").exists()


def test_cli_clean_does_not_remove_hidden_local_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

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
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

    assert workspace_commands.fermilink_init_main([str(workdir)]) == 0
    _assert_points_to(workdir / "src", payload_root / "src")
    assert (workdir / "skills").is_dir()
    assert not (workdir / "skills").is_symlink()
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")

    assert workspace_commands.fermilink_clean_main([str(workdir)]) == 0
    assert not (workdir / "src").exists()
    assert not (workdir / "skills").exists()
    assert not (workdir / "CLAUDE.md").exists()
    assert not (workdir / "GEMINI.md").exists()


def test_cli_init_replaces_managed_skills_symlink_with_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

    legacy_skills = workdir / "skills"
    legacy_skills.symlink_to(payload_root / "skills", target_is_directory=True)

    assert cli.main(["init", str(workdir)]) == 0
    assert legacy_skills.is_dir()
    assert not legacy_skills.is_symlink()
    assert (legacy_skills / "README.md").read_text(encoding="utf-8") == "skills\n"


def test_cli_init_package_mode_creates_local_package_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scipkg_root, _package_root = _install_package_fixture(
        tmp_path,
        package_id="solver",
    )
    installed_root = scipkg_root / "packages" / "solver"
    _configure_software_template(monkeypatch, tmp_path)
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)

    assert cli.main(["init", "solver"]) == 0
    assert (workdir / "AGENTS.md").read_text(encoding="utf-8") == (
        SOFTWARE_TEMPLATE_AGENTS_TEXT
    )
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "README.md", installed_root / "README.md")
    _assert_points_to(workdir / "src", installed_root / "src")
    skills_path = workdir / "skills"
    assert skills_path.is_dir()
    assert not skills_path.is_symlink()
    assert (skills_path / "README.md").read_text(encoding="utf-8") == (
        "package skills\n"
    )
    assert not (workdir / "public").exists()

    manifest = runner_scipkg.load_workspace_manifest(workdir)
    assert isinstance(manifest, dict)
    assert manifest["workspace_mode"] == "package_init"
    assert manifest["package_id"] == "solver"
    assert manifest["package_workflow_type"] == "simulation"
    linked_entries = {
        item["name"]: item
        for item in manifest["linked_entries"]
        if isinstance(item, dict)
    }
    assert linked_entries["skills"]["mode"] == "copy"

    assert cli.main(["init", "solver"]) == 0
    assert skills_path.is_dir()
    assert not skills_path.is_symlink()


def test_cli_init_package_mode_uses_experiment_agents_for_experiment_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scipkg_root, _package_root = _install_package_fixture(
        tmp_path,
        package_id="measurement",
        workflow_type="experiment",
    )
    _configure_software_template(monkeypatch, tmp_path)
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)

    assert cli.main(["init", "measurement"]) == 0
    assert (workdir / "AGENTS.md").read_text(encoding="utf-8") == load_exploop_guide()
    _assert_points_to(workdir / "CLAUDE.md", workdir / "AGENTS.md")
    _assert_points_to(workdir / "GEMINI.md", workdir / "AGENTS.md")
    skills_path = workdir / "skills"
    assert skills_path.is_dir()
    assert not skills_path.is_symlink()
    assert (skills_path / "README.md").read_text(encoding="utf-8") == (
        "package skills\n"
    )

    manifest = runner_scipkg.load_workspace_manifest(workdir)
    assert isinstance(manifest, dict)
    assert manifest["workspace_mode"] == "package_init"
    assert manifest["package_id"] == "measurement"
    assert manifest["package_workflow_type"] == "experiment"
    linked_entries = {
        item["name"]: item
        for item in manifest["linked_entries"]
        if isinstance(item, dict)
    }
    assert linked_entries["skills"]["mode"] == "copy"


def test_cli_init_package_mode_replaces_legacy_skills_symlink_with_copy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scipkg_root, _package_root = _install_package_fixture(
        tmp_path,
        package_id="solver",
    )
    installed_root = scipkg_root / "packages" / "solver"
    _configure_software_template(monkeypatch, tmp_path)
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "skills").symlink_to(
        installed_root / "skills",
        target_is_directory=True,
    )
    monkeypatch.chdir(workdir)

    assert cli.main(["init", "solver"]) == 0
    skills_path = workdir / "skills"
    assert skills_path.is_dir()
    assert not skills_path.is_symlink()
    assert (skills_path / "README.md").read_text(encoding="utf-8") == (
        "package skills\n"
    )


def test_cli_clean_package_mode_removes_only_package_init_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scipkg_root, package_root = _install_package_fixture(tmp_path, package_id="solver")
    _configure_software_template(monkeypatch, tmp_path)
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)

    assert cli.main(["init", "solver"]) == 0
    (workdir / "notes.txt").write_text("keep me\n", encoding="utf-8")

    assert cli.main(["clean"]) == 0
    assert not (workdir / "AGENTS.md").exists()
    assert not (workdir / "CLAUDE.md").exists()
    assert not (workdir / "GEMINI.md").exists()
    assert not (workdir / "README.md").exists()
    assert not (workdir / "src").exists()
    assert not (workdir / "skills").exists()
    assert not runner_scipkg.workspace_manifest_path(workdir).exists()
    assert (workdir / "notes.txt").read_text(encoding="utf-8") == "keep me\n"
    assert package_root.exists()


def test_cli_init_package_mode_entry_conflict_requires_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scipkg_root, _package_root = _install_package_fixture(tmp_path, package_id="solver")
    installed_root = scipkg_root / "packages" / "solver"
    _configure_software_template(monkeypatch, tmp_path)
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(workdir)
    (workdir / "src").write_text("local src placeholder\n", encoding="utf-8")

    assert cli.main(["init", "solver"]) == 2
    assert "Conflict at" in capsys.readouterr().err

    assert cli.main(["init", "solver", "--force"]) == 0
    _assert_points_to(workdir / "src", installed_root / "src")


def test_cli_clean_classic_mode_ignores_unmarked_package_manifest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    payload_root: Path,
) -> None:
    workdir = tmp_path / "workspace"
    workdir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(workspace_payload, "resolve_payload_root", lambda: payload_root)

    assert cli.main(["init", str(workdir)]) == 0
    runner_scipkg.save_workspace_manifest(
        workdir,
        {
            "version": 1,
            "package_id": "solver",
            "linked_entries": [
                {"name": "src", "mode": "symlink", "source": "/tmp/src"}
            ],
        },
    )

    assert cli.main(["clean", str(workdir)]) == 0
    assert not (workdir / "README.md").exists()
    assert not (workdir / "AGENTS.md").exists()
    assert not (workdir / "src").exists()


def test_cli_hpc_creates_default_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    profile = home / "HPC_PROFILE.json"
    assert not profile.exists()

    assert cli.main(["hpc"]) == 0
    assert profile.is_file()
    payload = json.loads(profile.read_text(encoding="utf-8"))
    assert payload["slurm_default_partition"] == "shared"
    assert payload["slurm_defaults"]
    assert payload["slurm_resource_policy"]

    first_text = profile.read_text(encoding="utf-8")
    assert cli.main(["hpc"]) == 0
    assert profile.read_text(encoding="utf-8") == first_text


def test_cli_hpc_migrates_legacy_default_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    legacy = home / "hpc_profile.json"
    legacy.write_text(
        json.dumps(
            {
                "slurm_default_partition": "debug",
                "slurm_defaults": "--nodes=1 --ntasks=4 --time=00:10:00",
                "slurm_resource_policy": "legacy",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert cli.main(["hpc"]) == 0
    canonical = home / "HPC_PROFILE.json"
    assert canonical.is_file()
    payload = json.loads(canonical.read_text(encoding="utf-8"))
    assert payload["slurm_default_partition"] == "debug"
    assert payload["slurm_defaults"] == "--nodes=1 --ntasks=4 --time=00:10:00"
    assert payload["slurm_resource_policy"] == "legacy"


def test_cli_hpc_set_installs_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    source = tmp_path / "custom_profile.json"
    source.write_text(
        json.dumps(
            {
                "slurm_default_partition": "gpu",
                "slurm_defaults": "--nodes=1 --gpus=1 --time=02:00:00",
                "slurm_resource_policy": "Prefer gpu queue",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert cli.main(["hpc", "set", str(source)]) == 0
    profile = home / "HPC_PROFILE.json"
    payload = json.loads(profile.read_text(encoding="utf-8"))
    assert payload["slurm_default_partition"] == "gpu"
    assert payload["slurm_defaults"] == "--nodes=1 --gpus=1 --time=02:00:00"
    assert payload["slurm_resource_policy"] == "Prefer gpu queue"


def test_cli_hpc_set_rejects_invalid_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / ".fermilink"
    monkeypatch.setenv("FERMILINK_HOME", str(home))
    source = tmp_path / "invalid_profile.json"
    source.write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert cli.main(["hpc", "set", str(source)]) == 2
    assert "missing required `slurm_resource_policy`" in capsys.readouterr().err
