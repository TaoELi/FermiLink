from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.packages.curated_channels import ChannelPackage, ChannelPackageVersion


def _git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=True,
    )
    return (completed.stdout or "").strip()


def _write_mock_benchmark_files(repo_dir: Path) -> Path:
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "mock_bench.py").write_text(
        (
            "from __future__ import annotations\n"
            "\n"
            "import argparse\n"
            "import json\n"
            "from pathlib import Path\n"
            "\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--benchmark', required=True)\n"
            "    parser.parse_args()\n"
            "    repo_dir = Path(__file__).resolve().parent.parent\n"
            "    solver_text = (repo_dir / 'solver.py').read_text(encoding='utf-8')\n"
            "    metric = 10.0\n"
            "    if 'FAST' in solver_text:\n"
            "        metric = 8.0\n"
            "    elif 'SLOW' in solver_text:\n"
            "        metric = 12.0\n"
            "    payload = {\n"
            "        'benchmark_id': 'mock-solver',\n"
            "        'correctness_ok': True,\n"
            "        'summary_metrics': {\n"
            "            'weighted_median_wall_seconds': metric,\n"
            "            'weighted_median_scf_iterations': 5.0,\n"
            "            'peak_rss_mb': 32.0,\n"
            "            'total_failures': 0,\n"
            "        },\n"
            "        'cases': [\n"
            "            {\n"
            "                'id': 'case-1',\n"
            "                'converged': True,\n"
            "                'total_energy_hartree': -1.0,\n"
            "                'density_matrix': [1.0, 0.0],\n"
            "                'mo_energies': [-0.5, 0.2],\n"
            "            }\n"
            "        ],\n"
            "    }\n"
            "    print(json.dumps(payload, sort_keys=True))\n"
            "    return 0\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
        ),
        encoding="utf-8",
    )
    benchmark_path = scripts_dir / "benchmark.yaml"
    benchmark_path.write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: mock-solver\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - solver.py\n"
            "  immutable_paths:\n"
            "    - scripts/**\n"
            "controller:\n"
            "  timeout_seconds: 30\n"
            "  warmup_runs: 0\n"
            "  measured_runs: 1\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "    direction: minimize\n"
            "    min_relative_improvement: 0.05\n"
            "campaign:\n"
            "  max_iterations: 1\n"
            "  stop_on_consecutive_rejections: 1\n"
            "correctness:\n"
            "  require_all_cases_converged: true\n"
            "  max_abs_energy_delta_hartree: 1.0e-9\n"
            "  max_abs_dm_rms_delta: 1.0e-9\n"
            "  max_abs_mo_energy_rms_delta: 1.0e-9\n"
            "runtime:\n"
            "  command:\n"
            f'    - "{sys.executable}"\n'
            "    - scripts/mock_bench.py\n"
            "    - --benchmark\n"
            '    - "{benchmark}"\n'
        ),
        encoding="utf-8",
    )
    return benchmark_path


def _init_optimize_repo(
    tmp_path: Path, *, with_skills: bool = True
) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "solver.py").write_text("MODE = 'BASELINE'\n", encoding="utf-8")
    if with_skills:
        (repo_dir / "skills").mkdir(parents=True, exist_ok=True)
        (repo_dir / "skills" / "README.md").write_text("skills", encoding="utf-8")
    benchmark_path = _write_mock_benchmark_files(repo_dir)
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "add", ".")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "initial",
    )
    return repo_dir, benchmark_path


def test_optimize_parser_supports_core_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "optimize",
            "pyscf",
            "/tmp/pyscf",
            "--benchmark",
            "/tmp/pyscf/scripts/benchmark.yaml",
            "--skills-source",
            "channel",
            "--channel",
            "skilled-scipkg",
            "--max-iterations",
            "5",
            "--forever",
        ]
    )
    assert args.command == "optimize"
    assert args.package_id == "pyscf"
    assert args.project_path == "/tmp/pyscf"
    assert args.benchmark == "/tmp/pyscf/scripts/benchmark.yaml"
    assert args.skills_source == "channel"
    assert args.channel == "skilled-scipkg"
    assert args.max_iterations == 5
    assert args.forever is True


def test_optimize_plan_only_initializes_campaign(tmp_path: Path) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)

    code = cli.main(
        [
            "optimize",
            "mockpkg",
            str(repo_dir),
            "--benchmark",
            str(benchmark_path),
            "--skills-source",
            "existing",
            "--plan-only",
        ]
    )

    assert code == 0
    optimize_root = repo_dir / ".fermilink-optimize"
    assert (optimize_root / "program.md").exists()
    assert (optimize_root / "memory.md").exists()
    assert (optimize_root / "results.tsv").exists()
    state = json.loads((optimize_root / "state.json").read_text(encoding="utf-8"))
    assert state["branch"] == "fermilink-optimize/mockpkg"
    assert state["accepted_count"] == 0
    assert state["rejected_count"] == 0


def test_optimize_accepts_better_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)

    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    def fake_run_exec_chat_turn(**kwargs):
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": "<experiment_description>fast path</experiment_description>",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    code = cli.main(
        [
            "optimize",
            "mockpkg",
            str(repo_dir),
            "--benchmark",
            str(benchmark_path),
            "--skills-source",
            "existing",
            "--max-iterations",
            "1",
        ]
    )

    assert code == 0
    assert "FAST" in (repo_dir / "solver.py").read_text(encoding="utf-8")
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1
    assert state["rejected_count"] == 0
    assert state["incumbent_metrics"]["summary_metrics"][
        "weighted_median_wall_seconds"
    ] == pytest.approx(8.0)
    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\tbaseline\t" in results_text
    assert "\taccepted\t" in results_text
    assert "fast path" in results_text
    assert _git(repo_dir, "log", "--format=%s", "-1") == (
        "fermilink optimize iter 1: fast path"
    )


def test_optimize_rejects_worse_candidate_and_restores_repo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)

    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    def fake_run_exec_chat_turn(**kwargs):
        (repo_dir / "solver.py").write_text("MODE = 'SLOW'\n", encoding="utf-8")
        return {
            "assistant_text": "<experiment_description>slow path</experiment_description>",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    code = cli.main(
        [
            "optimize",
            "mockpkg",
            str(repo_dir),
            "--benchmark",
            str(benchmark_path),
            "--skills-source",
            "existing",
            "--max-iterations",
            "1",
        ]
    )

    assert code == 0
    assert (repo_dir / "solver.py").read_text(encoding="utf-8") == "MODE = 'BASELINE'\n"
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 0
    assert state["rejected_count"] == 1
    assert state["incumbent_commit"] == state["baseline_commit"]
    memory_text = (repo_dir / ".fermilink-optimize" / "memory.md").read_text(
        encoding="utf-8"
    )
    assert "- status: baseline" in memory_text
    assert "slow path" in memory_text
    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\trejected\t" in results_text


def test_optimize_channel_bootstraps_skills(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path, with_skills=False)
    scipkg_root = tmp_path / "scientific_packages"
    monkeypatch.setenv("FERMILINK_SCIPKG_ROOT", str(scipkg_root))

    monkeypatch.setattr(
        cli,
        "resolve_curated_package",
        lambda package_id, channel: ChannelPackage(
            package_id=package_id,
            zip_url="https://example.invalid/mockpkg.zip",
            title="MockPkg",
            default_version="v1",
            versions=(
                ChannelPackageVersion(
                    version_id="v1",
                    source_archive_url="https://example.invalid/mockpkg.zip",
                    verified=True,
                ),
            ),
        ),
    )
    monkeypatch.setattr(
        cli,
        "select_package_version",
        lambda curated, version_id=None: curated.versions[0],
    )

    install_calls: list[str] = []

    def fake_install_from_zip(
        root: Path,
        package_id: str,
        *,
        zip_url: str,
        title: str | None,
        activate: bool,
        force: bool,
        max_zip_bytes: int,
    ) -> dict[str, object]:
        install_calls.append(zip_url)
        managed_root = root / "packages" / package_id / "skills"
        managed_root.mkdir(parents=True, exist_ok=True)
        (managed_root / "README.md").write_text("managed skills", encoding="utf-8")
        return {"package_id": package_id}

    monkeypatch.setattr(cli, "install_from_zip", fake_install_from_zip)

    code = cli.main(
        [
            "optimize",
            "mockpkg",
            str(repo_dir),
            "--benchmark",
            str(benchmark_path),
            "--skills-source",
            "channel",
            "--require-verified",
            "--plan-only",
        ]
    )

    assert code == 0
    assert install_calls == ["https://example.invalid/mockpkg.zip"]
    assert (repo_dir / "skills" / "README.md").read_text(encoding="utf-8") == (
        "managed skills"
    )
