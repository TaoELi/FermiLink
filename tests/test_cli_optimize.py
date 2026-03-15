from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli import optimize_git
from fermilink.cli import optimize_controller
from fermilink.cli import optimize_state
from fermilink.cli.commands import sessions as session_commands
from fermilink.cli.commands import workflows as workflow_commands
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


def _write_mock_benchmark_files(
    repo_dir: Path,
    *,
    submission_mode: str | None = None,
) -> Path:
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
            "    guardrail_errors = []\n"
            "    if 'FAST' in solver_text:\n"
            "        metric = 8.0\n"
            "    elif 'BROKEN' in solver_text:\n"
            "        metric = 7.0\n"
            "    elif 'SLOW' in solver_text:\n"
            "        metric = 12.0\n"
            "    elif 'REGRESS' in solver_text:\n"
            "        metric = 11.0\n"
            "        guardrail_errors = ['weighted_median_wall_seconds regressed vs incumbent']\n"
            "    payload = {\n"
            "        'benchmark_id': 'mock-solver',\n"
            "        'correctness_ok': 'BROKEN' not in solver_text,\n"
            "        'summary_metrics': {\n"
            "            'weighted_median_wall_seconds': metric,\n"
            "            'weighted_median_scf_iterations': 5.0,\n"
            "            'peak_rss_mb': 32.0,\n"
            "            'total_failures': 0,\n"
            "        },\n"
            "        'cases': [\n"
            "            {\n"
            "                'id': 'case-1',\n"
            "                'converged': 'BROKEN' not in solver_text,\n"
            "                'total_energy_hartree': -1.0 if 'BROKEN' not in solver_text else -0.8,\n"
            "                'density_matrix': [1.0, 0.0] if 'BROKEN' not in solver_text else [0.0, 1.0],\n"
            "                'mo_energies': [-0.5, 0.2] if 'BROKEN' not in solver_text else [0.2, -0.5],\n"
            "                'error': '' if 'BROKEN' not in solver_text else 'forced correctness failure',\n"
            "            }\n"
            "        ],\n"
            "    }\n"
            "    if guardrail_errors:\n"
            "        payload['guardrail_errors'] = guardrail_errors\n"
            "    print(json.dumps(payload, sort_keys=True))\n"
            "    return 0\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
        ),
        encoding="utf-8",
    )
    if submission_mode in {"pid", "slurm"}:
        (scripts_dir / "mock_submit_bench.py").write_text(
            (
                "from __future__ import annotations\n"
                "\n"
                "import argparse\n"
                "import json\n"
                "import subprocess\n"
                "import sys\n"
                "from pathlib import Path\n"
                "\n"
                "\n"
                "def _payload(repo_dir: Path) -> dict[str, object]:\n"
                "    solver_text = (repo_dir / 'solver.py').read_text(encoding='utf-8')\n"
                "    metric = 10.0\n"
                "    guardrail_errors = []\n"
                "    if 'FAST' in solver_text:\n"
                "        metric = 8.0\n"
                "    elif 'BROKEN' in solver_text:\n"
                "        metric = 7.0\n"
                "    elif 'SLOW' in solver_text:\n"
                "        metric = 12.0\n"
                "    elif 'REGRESS' in solver_text:\n"
                "        metric = 11.0\n"
                "        guardrail_errors = ['weighted_median_wall_seconds regressed vs incumbent']\n"
                "    payload = {\n"
                "        'benchmark_id': 'mock-solver',\n"
                "        'correctness_ok': 'BROKEN' not in solver_text,\n"
                "        'summary_metrics': {\n"
                "            'weighted_median_wall_seconds': metric,\n"
                "            'weighted_median_scf_iterations': 5.0,\n"
                "            'peak_rss_mb': 32.0,\n"
                "            'total_failures': 0,\n"
                "        },\n"
                "        'cases': [\n"
                "            {\n"
                "                'id': 'case-1',\n"
                "                'converged': 'BROKEN' not in solver_text,\n"
                "                'total_energy_hartree': -1.0 if 'BROKEN' not in solver_text else -0.8,\n"
                "                'density_matrix': [1.0, 0.0] if 'BROKEN' not in solver_text else [0.0, 1.0],\n"
                "                'mo_energies': [-0.5, 0.2] if 'BROKEN' not in solver_text else [0.2, -0.5],\n"
                "                'error': '' if 'BROKEN' not in solver_text else 'forced correctness failure',\n"
                "            }\n"
                "        ],\n"
                "    }\n"
                "    if guardrail_errors:\n"
                "        payload['guardrail_errors'] = guardrail_errors\n"
                "    return payload\n"
                "\n"
                "\n"
                "def main() -> int:\n"
                "    parser = argparse.ArgumentParser()\n"
                "    parser.add_argument('--benchmark', required=True)\n"
                "    parser.add_argument('--mode', choices=('pid', 'slurm'), required=True)\n"
                "    args = parser.parse_args()\n"
                "    repo_dir = Path(__file__).resolve().parent.parent\n"
                "    metrics_path = repo_dir / '.fermilink-optimize' / 'latest_metrics.json'\n"
                "    metrics_path.parent.mkdir(parents=True, exist_ok=True)\n"
                "    payload = _payload(repo_dir)\n"
                "    metrics_text = json.dumps(payload, sort_keys=True)\n"
                "    if args.mode == 'pid':\n"
                "        writer_script = (\n"
                "            'from pathlib import Path\\n'\n"
                "            'import time\\n'\n"
                "            'time.sleep(0.2)\\n'\n"
                "            f\"Path({str(metrics_path)!r}).write_text({metrics_text!r}, encoding='utf-8')\\n\"\n"
                "        )\n"
                "        proc = subprocess.Popen([sys.executable, '-c', writer_script])\n"
                "        print(f'<pid_number>{proc.pid}</pid_number>')\n"
                "        return 0\n"
                "    metrics_path.write_text(metrics_text, encoding='utf-8')\n"
                "    print('<slurm_job_number>12345</slurm_job_number>')\n"
                "    return 0\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    raise SystemExit(main())\n"
            ),
            encoding="utf-8",
        )
    benchmark_path = scripts_dir / "benchmark.yaml"
    runtime_block = (
        "runtime:\n"
        "  mode: direct\n"
        "  command:\n"
        f'    - "{sys.executable}"\n'
        "    - scripts/mock_bench.py\n"
        "    - --benchmark\n"
        '    - "{benchmark}"\n'
    )
    if submission_mode in {"pid", "slurm"}:
        runtime_block = (
            "runtime:\n"
            "  mode: submit_poll\n"
            "  command:\n"
            f'    - "{sys.executable}"\n'
            "    - scripts/mock_submit_bench.py\n"
            "    - --benchmark\n"
            '    - "{benchmark}"\n'
            "    - --mode\n"
            f"    - {submission_mode}\n"
            "  result_json_path: .fermilink-optimize/latest_metrics.json\n"
            "  poll_interval_seconds: 0.01\n"
            "  max_poll_seconds: 5\n"
        )
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
            f"{runtime_block}"
        ),
        encoding="utf-8",
    )
    return benchmark_path


def _init_optimize_repo(
    tmp_path: Path,
    *,
    with_skills: bool = True,
    benchmark_runtime: str = "direct",
) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "solver.py").write_text("MODE = 'BASELINE'\n", encoding="utf-8")
    if with_skills:
        (repo_dir / "skills").mkdir(parents=True, exist_ok=True)
        (repo_dir / "skills" / "README.md").write_text("skills", encoding="utf-8")
    if benchmark_runtime not in {"direct", "submit_poll_pid", "submit_poll_slurm"}:
        raise ValueError(f"Unsupported benchmark runtime: {benchmark_runtime}")
    submission_mode = None
    if benchmark_runtime == "submit_poll_pid":
        submission_mode = "pid"
    if benchmark_runtime == "submit_poll_slurm":
        submission_mode = "slurm"
    benchmark_path = _write_mock_benchmark_files(
        repo_dir,
        submission_mode=submission_mode,
    )
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
            "--worker-max-iterations",
            "7",
            "--worker-wait-seconds",
            "2.5",
            "--worker-max-wait-seconds",
            "20",
            "--worker-pid-stall-seconds",
            "30",
            "--hpc-profile",
            "scripts/hpc_profile_anvil.json",
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
    assert args.worker_max_iterations == 7
    assert args.worker_wait_seconds == 2.5
    assert args.worker_max_wait_seconds == 20
    assert args.worker_pid_stall_seconds == 30
    assert args.hpc_profile == "scripts/hpc_profile_anvil.json"
    assert args.forever is True


def test_optimize_parser_supports_status_mode() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "optimize",
            "status",
            "/tmp/repo",
            "--tail",
            "40",
        ]
    )
    assert args.command == "optimize"
    assert args.package_id == "status"
    assert args.project_path == "/tmp/repo"
    assert args.tail == 40


def test_load_benchmark_rejects_unknown_correctness_mode(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: mock\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - src/**\n"
            "controller:\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "correctness:\n"
            "  mode: unknown_mode\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - -c\n"
            "    - print('ok')\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(cli.PackageError, match="correctness.mode"):
        optimize_controller._load_benchmark(benchmark_path)


def test_compare_correctness_runner_only_uses_generic_case_checks() -> None:
    benchmark_payload = {
        "correctness": {
            "mode": "runner_only",
            "require_all_cases_converged": True,
        }
    }
    incumbent_metrics = {
        "cases": [
            {
                "id": "case-1",
                "converged": True,
                "physics_payload": {"energy": -1.0, "forces": [0.1, 0.2, 0.3]},
            }
        ]
    }
    candidate_metrics = {
        "cases": [
            {
                "id": "case-1",
                "converged": True,
                "physics_payload": {"energy": -9.0, "forces": [9.1, 9.2, 9.3]},
            }
        ]
    }

    comparison = optimize_controller._compare_correctness(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        candidate_metrics=candidate_metrics,
    )

    assert comparison["ok"] is True
    assert comparison["errors"] == []
    assert comparison["mode"] == "runner_only"


def test_compare_correctness_field_tolerances_works_for_generic_fields() -> None:
    benchmark_payload = {
        "correctness": {
            "mode": "field_tolerances",
            "field_tolerances": [
                {
                    "field": "outputs.force_norm",
                    "abs_delta": 0.05,
                }
            ],
        }
    }
    incumbent_metrics = {
        "cases": [
            {"id": "case-1", "converged": True, "outputs": {"force_norm": 1.0}}
        ]
    }
    passing_candidate = {
        "cases": [
            {"id": "case-1", "converged": True, "outputs": {"force_norm": 1.04}}
        ]
    }
    failing_candidate = {
        "cases": [
            {"id": "case-1", "converged": True, "outputs": {"force_norm": 1.2}}
        ]
    }

    passing = optimize_controller._compare_correctness(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        candidate_metrics=passing_candidate,
    )
    failing = optimize_controller._compare_correctness(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        candidate_metrics=failing_candidate,
    )

    assert passing["ok"] is True
    assert failing["ok"] is False
    assert "force_norm abs_delta exceeds threshold" in "; ".join(failing["errors"])


def test_optimize_quick_mode_plan_only_scaffolds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_dir, _benchmark_path = _init_optimize_repo(tmp_path)
    prompt_path = repo_dir / "prompt.md"
    prompt_path.write_text(
        (
            "# Quick optimize prompt\n"
            "\n"
            "```bash\n"
            "python -c \"print('ok')\"\n"
            "```\n"
        ),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "prompt.md")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add prompt",
    )
    monkeypatch.chdir(repo_dir)

    code = cli.main(
        [
            "optimize",
            "prompt.md",
            "--plan-only",
        ]
    )

    assert code == 0
    autogen_root = repo_dir / ".fermilink-optimize" / "autogen"
    assert (autogen_root / "benchmark.yaml").exists()
    assert (autogen_root / "benchmark_runner.py").exists()
    assert (autogen_root / "submit_poll_launcher.py").exists()
    assert (autogen_root / "setup_env.sh").exists()
    assert (autogen_root / "run_optimize.sh").exists()
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["iteration"] == 0
    benchmark_text = (autogen_root / "benchmark.yaml").read_text(encoding="utf-8")
    assert "mode: direct" in benchmark_text
    assert "weighted_median_wall_seconds" in benchmark_text
    benchmark_payload = yaml.safe_load(benchmark_text)
    assert benchmark_payload["correctness"]["mode"] == "runner_only"


def test_optimize_quick_mode_reuses_existing_autogen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir, _benchmark_path = _init_optimize_repo(tmp_path)
    prompt_path = repo_dir / "prompt.md"
    prompt_path.write_text(
        (
            "# Prompt\n"
            "\n"
            "```bash\n"
            "python -c \"print('ok')\"\n"
            "```\n"
        ),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "prompt.md")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add prompt",
    )
    monkeypatch.chdir(repo_dir)

    code_first = cli.main(["optimize", "prompt.md", "--plan-only"])
    assert code_first == 0

    benchmark_path = repo_dir / ".fermilink-optimize" / "autogen" / "benchmark.yaml"
    marker = "# user-edit-marker\n"
    benchmark_path.write_text(
        benchmark_path.read_text(encoding="utf-8") + marker,
        encoding="utf-8",
    )

    code_second = cli.main(["optimize", "prompt.md", "--plan-only"])
    assert code_second == 0
    assert marker in benchmark_path.read_text(encoding="utf-8")


def test_optimize_quick_mode_compiles_skills_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir, _benchmark_path = _init_optimize_repo(tmp_path, with_skills=False)
    prompt_path = repo_dir / "prompt.md"
    prompt_path.write_text(
        (
            "# Prompt\n"
            "\n"
            "```bash\n"
            "python -c \"print('ok')\"\n"
            "```\n"
        ),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "prompt.md")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add prompt",
    )
    monkeypatch.chdir(repo_dir)

    compile_calls: list[str] = []

    def fake_compile(args: argparse.Namespace) -> int:
        compile_calls.append(str(args.project_path))
        skills_root = repo_dir / "skills"
        skills_root.mkdir(parents=True, exist_ok=True)
        (skills_root / "README.md").write_text("skills", encoding="utf-8")
        return 0

    monkeypatch.setattr(cli, "_cmd_compile", fake_compile)

    code = cli.main(["optimize", "prompt.md", "--plan-only"])

    assert code == 0
    assert compile_calls == [str(repo_dir)]
    assert (repo_dir / "skills" / "README.md").read_text(encoding="utf-8") == "skills"


def test_optimize_quick_mode_seeds_from_reference_templates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir = tmp_path / "cpp_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "skills").mkdir(parents=True, exist_ok=True)
    (repo_dir / "skills" / "README.md").write_text("skills", encoding="utf-8")
    (repo_dir / "CMakeLists.txt").write_text(
        "cmake_minimum_required(VERSION 3.10)\nproject(Mock LANGUAGES CXX)\n",
        encoding="utf-8",
    )
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "main.cpp").write_text(
        "int main() { return 0; }\n",
        encoding="utf-8",
    )
    prompt_path = repo_dir / "prompt.md"
    prompt_path.write_text(
        (
            "# Optimize request\n"
            "\n"
            "Improve force-evaluation throughput while preserving correctness.\n"
        ),
        encoding="utf-8",
    )

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
    monkeypatch.chdir(repo_dir)

    code = cli.main(["optimize", "prompt.md", "--plan-only"])

    assert code == 0
    autogen_root = repo_dir / ".fermilink-optimize" / "autogen"
    benchmark_payload = yaml.safe_load(
        (autogen_root / "benchmark.yaml").read_text(encoding="utf-8")
    )
    assert isinstance(benchmark_payload, dict)
    autogen = benchmark_payload.get("autogen")
    assert isinstance(autogen, dict)
    reference_examples = autogen.get("reference_examples")
    assert isinstance(reference_examples, dict)
    assert str(reference_examples.get("benchmark") or "").endswith(
        "scripts/cpp-lammps-tip4p-force-eval-benchmark.yaml"
    )
    assert str(reference_examples.get("runner") or "").endswith(
        "scripts/cpp-lammps-tip4p-force-eval-bench.sh"
    )
    runtime = benchmark_payload.get("runtime")
    assert isinstance(runtime, dict)
    env = runtime.get("env")
    assert isinstance(env, dict)
    assert env.get("OMP_NUM_THREADS") == "1"
    cases = benchmark_payload.get("cases")
    assert isinstance(cases, list)
    assert cases
    assert "command_preview" in cases[0]
    assert "lmp -in" in str(cases[0].get("command_preview") or "")
    manifest = json.loads((autogen_root / "quick_mode.json").read_text(encoding="utf-8"))
    assert manifest["command_source"] == "default"
    assert str(manifest["reference_examples"]["benchmark"]).endswith(
        "scripts/cpp-lammps-tip4p-force-eval-benchmark.yaml"
    )


def test_optimize_status_reports_campaign_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
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
    capsys.readouterr()
    optimize_state.append_result(
        optimize_state.results_path(repo_dir),
        iteration=0,
        commit="abcdef123456",
        status="baseline",
        primary_metric_name="weighted_median_wall_seconds",
        primary_metric_value=10.0,
        description="baseline",
    )

    status_code = cli.main(
        [
            "optimize",
            "status",
            str(repo_dir),
            "--tail",
            "5",
            "--json",
        ]
    )
    assert status_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert payload["run_lock_status"] == "inactive"
    assert payload["runtime_mode"] == "direct"
    assert "baseline" in payload["recent_results"]


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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            memory_path = repo_dir / ".fermilink-optimize" / "memory.md"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + "\n### Iteration 1\n- lesson: fast path worked\n- next_hypothesis: refine it\n",
                encoding="utf-8",
            )
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>clear benchmark win</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert calls == ["worker", "controller"]
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
    assert "clear benchmark win" in results_text
    memory_text = (repo_dir / ".fermilink-optimize" / "memory.md").read_text(
        encoding="utf-8"
    )
    assert "lesson: fast path worked" in memory_text
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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            memory_path = repo_dir / ".fermilink-optimize" / "memory.md"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + "\n### Iteration 1\n- lesson: slower than incumbent\n- next_hypothesis: avoid extra overhead\n",
                encoding="utf-8",
            )
            return {
                "assistant_text": (
                    "<decision>REJECTED</decision>\n"
                    "<controller_summary>slower without compensating benefit</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'SLOW'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>slow path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert calls == ["worker", "controller"]
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
    assert "lesson: slower than incumbent" in memory_text
    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\trejected\t" in results_text
    assert "slower without compensating benefit" in results_text


def test_optimize_hard_reject_overrides_controller_accept(
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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            memory_path = repo_dir / ".fermilink-optimize" / "memory.md"
            memory_path.write_text(
                memory_path.read_text(encoding="utf-8")
                + "\n### Iteration 1\n- lesson: benchmark correctness failed\n- next_hypothesis: keep the speed idea but preserve physics\n",
                encoding="utf-8",
            )
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>thought the speedup looked promising</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'BROKEN'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>broken fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert calls == ["worker", "controller"]
    assert (repo_dir / "solver.py").read_text(encoding="utf-8") == "MODE = 'BASELINE'\n"
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 0
    assert state["rejected_count"] == 1
    assert state["incumbent_commit"] == state["baseline_commit"]
    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\tcorrectness_failure\t" in results_text
    memory_text = (repo_dir / ".fermilink-optimize" / "memory.md").read_text(
        encoding="utf-8"
    )
    assert "lesson: benchmark correctness failed" in memory_text


def test_optimize_guardrail_regression_reports_performance_rejection(
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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>controller would accept if no hard guards</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'REGRESS'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>regressing candidate</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert calls == ["worker", "controller"]
    assert (repo_dir / "solver.py").read_text(encoding="utf-8") == "MODE = 'BASELINE'\n"

    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\trejected\t" in results_text
    assert "\tcorrectness_failure\t" not in results_text
    assert "performance_regression" in results_text

    review_context = json.loads(
        (
            repo_dir
            / ".fermilink-optimize"
            / "runs"
            / "iter_0001"
            / "review_context.json"
        ).read_text(encoding="utf-8")
    )
    assert review_context.get("hard_reject") is True
    assert review_context.get("hard_reject_status") == "rejected"
    assert review_context.get("hard_reject_category") == "performance_regression"
    assert "performance_regression" in str(review_context.get("hard_reject_reason") or "")


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


def test_optimize_worker_loop_can_fix_candidate_before_benchmark(
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

    calls: list[str] = []
    worker_turn = {"count": 0}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>worker fixed the bug before benchmark</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        worker_turn["count"] += 1
        if worker_turn["count"] == 1:
            calls.append("worker1")
            (repo_dir / "solver.py").write_text("MODE = 'BROKEN'\n", encoding="utf-8")
            return {
                "assistant_text": (
                    "<experiment_description>initial buggy fast path</experiment_description>\n"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker2")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fixed fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--worker-max-iterations",
            "3",
            "--worker-wait-seconds",
            "0",
            "--worker-max-wait-seconds",
            "0",
        ]
    )

    assert code == 0
    assert calls == ["worker1", "worker2", "controller"]
    assert "FAST" in (repo_dir / "solver.py").read_text(encoding="utf-8")
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1
    assert state["rejected_count"] == 0


def test_optimize_rejects_incomplete_worker_without_controller(
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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>needs another debugging turn</experiment_description>\n"
            ),
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
            "--worker-max-iterations",
            "1",
            "--worker-wait-seconds",
            "0",
            "--worker-max-wait-seconds",
            "0",
        ]
    )

    assert code == 0
    assert calls == ["worker"]
    assert (repo_dir / "solver.py").read_text(encoding="utf-8") == "MODE = 'BASELINE'\n"
    results_text = (repo_dir / ".fermilink-optimize" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\tworker_incomplete\t" in results_text


def test_optimize_archives_worker_memory_and_skips_routing_overlay_and_completion_commit(
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
    monkeypatch.setattr(
        cli,
        "_resolve_exec_package_selection",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("optimize should not route packages through session logic")
        ),
    )
    monkeypatch.setattr(
        cli,
        "_overlay_exec_package",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("optimize should not apply session overlay logic")
        ),
    )

    completion_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        workflow_commands,
        "_workflow_completion_commit",
        lambda *, repo_dir, mode_name: completion_calls.append(
            (Path(repo_dir), str(mode_name))
        )
        or {"status": "noop", "sha": "", "error": "", "memory_only": "false"},
    )

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accepted</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert completion_calls == []
    assert (repo_dir / ".fermilink-optimize" / "worker_memory.md").exists()
    assert (
        repo_dir
        / ".fermilink-optimize"
        / "runs"
        / "iter_0001"
        / "worker_memory.md"
    ).exists()


def test_optimize_worker_hpc_profile_appends_execution_target_constraints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)
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

    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    captured: dict[str, object] = {}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accepted</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        captured["prompt"] = prompt
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--hpc-profile",
            str(repo_dir / "hpc_profile.json"),
            "--allow-dirty",
        ]
    )

    assert code == 0
    prompt = str(captured.get("prompt") or "")
    assert "Execution target constraints:" in prompt
    assert "execution_target: HPC SLURM." in prompt
    assert "slurm_default_partition: `shared`." in prompt


def test_optimize_worker_loop_handles_pid_waits(
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

    calls: list[str] = []
    worker_turn = {"count": 0}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>pid wait completed</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        worker_turn["count"] += 1
        if worker_turn["count"] == 1:
            calls.append("worker1")
            proc = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(0.2)"]
            )
            return {
                "assistant_text": (
                    f"submitted\n<pid_number>{proc.pid}</pid_number>\n"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker2")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path after local wait</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--worker-max-iterations",
            "3",
            "--worker-wait-seconds",
            "0.05",
            "--worker-max-wait-seconds",
            "5",
        ]
    )

    assert code == 0
    assert calls == ["worker1", "worker2", "controller"]


def test_optimize_worker_loop_handles_slurm_waits(
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
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)

    slurm_polls = {"count": 0}

    def fake_refresh_slurm_monitors(
        slurm_job_numbers: list[str],
        monitors: dict[str, object],
        *,
        now_monotonic: float,
        unknown_poll_limit: int,
    ) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]], dict[str, object]]:
        slurm_polls["count"] += 1
        if slurm_polls["count"] == 1:
            return list(slurm_job_numbers), [], [], {"12345": object()}
        return [], [], [], {}

    monkeypatch.setattr(
        session_commands,
        "_refresh_slurm_monitors",
        fake_refresh_slurm_monitors,
    )

    calls: list[str] = []
    worker_turn = {"count": 0}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>slurm wait completed</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        worker_turn["count"] += 1
        if worker_turn["count"] == 1:
            calls.append("worker1")
            return {
                "assistant_text": (
                    "submitted\n<slurm_job_number>12345</slurm_job_number>\n"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker2")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path after slurm wait</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--worker-max-iterations",
            "3",
            "--worker-wait-seconds",
            "0.01",
            "--worker-max-wait-seconds",
            "1",
        ]
    )

    assert code == 0
    assert calls == ["worker1", "worker2", "controller"]


def test_optimize_benchmark_submit_poll_handles_pid_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(
        tmp_path,
        benchmark_runtime="submit_poll_pid",
    )
    hpc_profile_path = tmp_path / "hpc_profile.json"
    hpc_profile_path.write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1",
                "slurm_resource_policy": "Prefer single node when possible",
            }
        )
        + "\n",
        encoding="utf-8",
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

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "planning the authoritative benchmark submission launcher" in prompt:
            calls.append("planner")
            return {
                "assistant_text": (
                    "<benchmark_launcher>"
                    "{\"command\": ["
                    f"\"{sys.executable}\", "
                    "\"scripts/mock_submit_bench.py\", "
                    "\"--benchmark\", "
                    "\"{benchmark}\", "
                    "\"--mode\", "
                    "\"pid\""
                    "]}"
                    "</benchmark_launcher>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>benchmark submit/poll succeeded</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--hpc-profile",
            str(hpc_profile_path),
            "--allow-dirty",
        ]
    )

    assert code == 0
    assert calls == ["planner", "worker", "controller"]
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1
    assert state["benchmark_launcher"]["source"] == "controller_agent"
    assert state["incumbent_metrics"]["summary_metrics"][
        "weighted_median_wall_seconds"
    ] == pytest.approx(8.0)
    assert (
        repo_dir
        / ".fermilink-optimize"
        / "runs"
        / "iter_0001"
        / "measured_1.result.metrics.json"
    ).exists()


def test_optimize_benchmark_submit_poll_handles_slurm_submission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(
        tmp_path,
        benchmark_runtime="submit_poll_slurm",
    )
    hpc_profile_path = tmp_path / "hpc_profile.json"
    hpc_profile_path.write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1",
                "slurm_resource_policy": "Use standard short queue",
            }
        )
        + "\n",
        encoding="utf-8",
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
    monkeypatch.setattr(session_commands, "_slurm_wait_tools_available", lambda: True)

    slurm_polls = {"count": 0}

    def fake_refresh_slurm_monitors(
        slurm_job_numbers: list[str],
        monitors: dict[str, object],
        *,
        now_monotonic: float,
        unknown_poll_limit: int,
    ) -> tuple[list[str], list[tuple[str, str]], list[tuple[str, str]], dict[str, object]]:
        slurm_polls["count"] += 1
        if slurm_polls["count"] % 2 == 1:
            return (
                list(slurm_job_numbers),
                [],
                [],
                {job_id: object() for job_id in slurm_job_numbers},
            )
        return [], [], [], {}

    monkeypatch.setattr(
        session_commands,
        "_refresh_slurm_monitors",
        fake_refresh_slurm_monitors,
    )

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "planning the authoritative benchmark submission launcher" in prompt:
            calls.append("planner")
            return {
                "assistant_text": (
                    "<benchmark_launcher>"
                    "{\"command\": ["
                    f"\"{sys.executable}\", "
                    "\"scripts/mock_submit_bench.py\", "
                    "\"--benchmark\", "
                    "\"{benchmark}\", "
                    "\"--mode\", "
                    "\"slurm\""
                    "]}"
                    "</benchmark_launcher>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        if "controller for a completed FermiLink optimize iteration" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accepted</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        calls.append("worker")
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--hpc-profile",
            str(hpc_profile_path),
            "--allow-dirty",
        ]
    )

    assert code == 0
    assert calls == ["planner", "worker", "controller"]
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1
    assert slurm_polls["count"] >= 4


def test_optimize_submit_poll_replans_launcher_after_infra_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(
        tmp_path,
        benchmark_runtime="submit_poll_pid",
    )
    hpc_profile_path = tmp_path / "hpc_profile.json"
    hpc_profile_path.write_text(
        json.dumps(
            {
                "slurm_default_partition": "shared",
                "slurm_defaults": "--nodes=1 --ntasks=1 --ntasks-per-node=1",
                "slurm_resource_policy": "Use resilient launcher fallback",
            }
        )
        + "\n",
        encoding="utf-8",
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

    planner_calls = {"count": 0}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "planning the authoritative benchmark submission launcher" in prompt:
            planner_calls["count"] += 1
            if planner_calls["count"] == 1:
                return {
                    "assistant_text": (
                        "<benchmark_launcher>"
                        "{\"command\": [\"definitely_not_a_real_binary_fermilink_test\"]}"
                        "</benchmark_launcher>"
                    ),
                    "return_code": 0,
                    "stderr": "",
                }
            return {
                "assistant_text": (
                    "<benchmark_launcher>"
                    "{\"command\": ["
                    f"\"{sys.executable}\", "
                    "\"scripts/mock_submit_bench.py\", "
                    "\"--benchmark\", "
                    "\"{benchmark}\", "
                    "\"--mode\", "
                    "\"pid\""
                    "]}"
                    "</benchmark_launcher>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accepted after launcher replan</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--hpc-profile",
            str(hpc_profile_path),
            "--allow-dirty",
        ]
    )

    assert code == 0
    assert planner_calls["count"] == 2
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1


def test_optimize_rejected_candidate_cleans_new_untracked_files(
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

    artifact_path = repo_dir / "controller_reject_artifact.tmp"

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            artifact_path.write_text("reject artifact", encoding="utf-8")
            return {
                "assistant_text": (
                    "<decision>REJECTED</decision>\n"
                    "<controller_summary>reject candidate</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "solver.py").write_text("MODE = 'SLOW'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>slow path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
    assert not artifact_path.exists()


def test_optimize_accepted_candidate_cleans_new_untracked_files(
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

    artifact_path = repo_dir / "controller_accept_artifact.tmp"
    real_list_untracked_paths = optimize_git.list_untracked_paths
    untracked_calls = {"count": 0}

    def fake_list_untracked_paths(repo_path: Path) -> list[str]:
        untracked_calls["count"] += 1
        if untracked_calls["count"] == 2:
            artifact_path.write_text("accept artifact", encoding="utf-8")
        return real_list_untracked_paths(repo_path)

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accept candidate</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(
        optimize_git,
        "list_untracked_paths",
        fake_list_untracked_paths,
    )
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
    assert not artifact_path.exists()


def test_optimize_cleanup_preserves_preexisting_untracked_entries(
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

    untracked_calls = {"count": 0}

    def fake_list_untracked_paths(_repo_dir: Path) -> list[str]:
        untracked_calls["count"] += 1
        if untracked_calls["count"] == 1:
            return ["preexisting.tmp"]
        return ["preexisting.tmp", "new_artifact.tmp"]

    cleanup_calls: list[list[str]] = []

    monkeypatch.setattr(
        optimize_git,
        "list_untracked_paths",
        fake_list_untracked_paths,
    )
    monkeypatch.setattr(
        optimize_git,
        "cleanup_paths",
        lambda _repo_dir, paths: cleanup_calls.append(list(paths)),
    )

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>accepted</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "solver.py").write_text("MODE = 'FAST'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<experiment_description>fast path</experiment_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
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
            "--allow-dirty",
        ]
    )

    assert code == 0
    assert cleanup_calls == [["new_artifact.tmp"]]
