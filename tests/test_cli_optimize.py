from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.cli import optimize_git
from fermilink.cli import optimize_controller
from fermilink.cli import optimize_prompts
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


def _write_solver_in_worker(kwargs: dict[str, object], *, mode: str) -> None:
    repo_dir_raw = str(kwargs.get("repo_dir") or "").strip()
    if not repo_dir_raw:
        raise AssertionError("missing repo_dir for worker turn")
    worker_repo = Path(repo_dir_raw)
    (worker_repo / "solver.py").write_text(f"MODE = '{mode}'\n", encoding="utf-8")


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
            "  mode: runner_only\n"
            "  require_all_cases_converged: true\n"
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


def _init_split_optimize_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo_dir = tmp_path / "repo-split"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "solver.py").write_text("MODE = 'BASELINE'\n", encoding="utf-8")
    (repo_dir / "skills").mkdir(parents=True, exist_ok=True)
    (repo_dir / "skills" / "README.md").write_text("skills", encoding="utf-8")
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "mock_split_bench.py").write_text(
        (
            "from __future__ import annotations\n"
            "\n"
            "import argparse\n"
            "import json\n"
            "import statistics\n"
            "from pathlib import Path\n"
            "\n"
            "import yaml\n"
            "\n"
            "\n"
            "BASELINE = {\n"
            "    'train-a': 10.0,\n"
            "    'train-b': 10.0,\n"
            "    'test-a': 10.0,\n"
            "    'test-b': 10.0,\n"
            "}\n"
            "\n"
            "\n"
            "def _case_time(case_id: str, mode_text: str) -> float:\n"
            "    baseline = float(BASELINE.get(case_id, 10.0))\n"
            "    if 'TRAIN_FAST' not in mode_text:\n"
            "        return baseline\n"
            "    if case_id.startswith('train-'):\n"
            "        return 1.0\n"
            "    return 20.0\n"
            "\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--benchmark', required=True)\n"
            "    parser.add_argument('--emit-json', action='store_true')\n"
            "    args = parser.parse_args()\n"
            "    benchmark_path = Path(args.benchmark).resolve()\n"
            "    payload = yaml.safe_load(benchmark_path.read_text(encoding='utf-8'))\n"
            "    if not isinstance(payload, dict):\n"
            "        raise SystemExit('invalid benchmark payload')\n"
            "    raw_cases = payload.get('cases')\n"
            "    cases = [item for item in raw_cases if isinstance(item, dict)] if isinstance(raw_cases, list) else []\n"
            "    repo_dir = Path(__file__).resolve().parent.parent\n"
            "    mode_text = (repo_dir / 'solver.py').read_text(encoding='utf-8')\n"
            "    case_results = []\n"
            "    for case in cases:\n"
            "        case_id = str(case.get('id') or 'case')\n"
            "        wall = _case_time(case_id, mode_text)\n"
            "        case_results.append(\n"
            "            {\n"
            "                'id': case_id,\n"
            "                'converged': True,\n"
            "                'wall_seconds': wall,\n"
            "                'error': '',\n"
            "            }\n"
            "        )\n"
            "    wall_values = [float(item.get('wall_seconds') or 0.0) for item in case_results]\n"
            "    median_wall = statistics.median(wall_values) if wall_values else 0.0\n"
            "    output = {\n"
            "        'benchmark_id': str(payload.get('benchmark_id') or 'mock-split'),\n"
            "        'correctness_ok': True,\n"
            "        'summary_metrics': {\n"
            "            'weighted_median_wall_seconds': float(median_wall),\n"
            "            'peak_rss_mb': 0.0,\n"
            "            'total_failures': 0,\n"
            "        },\n"
            "        'cases': case_results,\n"
            "    }\n"
            "    if 'TRAIN_FAST' in mode_text and any(\n"
            "        str(item.get('id') or '').startswith('test-') for item in case_results\n"
            "    ):\n"
            "        output['guardrail_errors'] = ['test-only regression detected']\n"
            "    print(json.dumps(output, sort_keys=True))\n"
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
            "benchmark_id: mock-split\n"
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
            "worker:\n"
            "  max_iterations: 2\n"
            "  wait_seconds: 0\n"
            "correctness:\n"
            "  mode: runner_only\n"
            "split:\n"
            "  train_case_ids:\n"
            "    - train-a\n"
            "    - train-b\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            f'    - "{sys.executable}"\n'
            "    - scripts/mock_split_bench.py\n"
            "    - --benchmark\n"
            '    - "{benchmark}"\n'
            "    - --emit-json\n"
            "cases:\n"
            "  - id: train-a\n"
            "    weight: 1.0\n"
            "  - id: train-b\n"
            "    weight: 1.0\n"
            "  - id: test-a\n"
            "    weight: 1.0\n"
            "  - id: test-b\n"
            "    weight: 1.0\n"
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


def test_load_benchmark_rejects_legacy_scf_correctness_keys(tmp_path: Path) -> None:
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
            "  require_all_cases_converged: true\n"
            "  max_abs_energy_delta_hartree: 1.0e-8\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - -c\n"
            "    - print('ok')\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(cli.PackageError, match="Legacy SCF correctness keys"):
        optimize_controller._load_benchmark(benchmark_path)


def test_load_benchmark_split_rejects_unknown_train_case_ids(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: split-mock\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - src/**\n"
            "controller:\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - -c\n"
            "    - print('ok')\n"
            "split:\n"
            "  train_case_ids:\n"
            "    - train-a\n"
            "cases:\n"
            "  - id: test-a\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(cli.PackageError, match="references unknown cases"):
        optimize_controller._load_benchmark(benchmark_path)


def test_load_benchmark_split_requires_controller_test_cases(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: split-mock\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - src/**\n"
            "controller:\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - -c\n"
            "    - print('ok')\n"
            "split:\n"
            "  train_case_ids:\n"
            "    - only-case\n"
            "cases:\n"
            "  - id: only-case\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        cli.PackageError, match="at least one controller-only test case"
    ):
        optimize_controller._load_benchmark(benchmark_path)


def test_load_benchmark_rejects_invalid_runtime_pre_commands(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.yaml"
    benchmark_path.write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: pre-command-shape\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - src/**\n"
            "controller:\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "runtime:\n"
            "  mode: direct\n"
            "  pre_commands:\n"
            "    - python -m pip install -e .\n"
            "  command:\n"
            "    - python\n"
            "    - -c\n"
            "    - print('ok')\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(cli.PackageError, match="runtime.pre_commands\\[1\\]"):
        optimize_controller._load_benchmark(benchmark_path)


def test_run_benchmark_suite_executes_runtime_pre_commands_once_per_suite(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo-pre-commands"
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "bench.py").write_text(
        (
            "from __future__ import annotations\n"
            "\n"
            "import argparse\n"
            "import json\n"
            "\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--benchmark', required=True)\n"
            "    parser.add_argument('--emit-json', action='store_true')\n"
            "    parser.parse_args()\n"
            "    payload = {\n"
            "        'benchmark_id': 'pre-command-test',\n"
            "        'correctness_ok': True,\n"
            "        'summary_metrics': {\n"
            "            'weighted_median_wall_seconds': 1.0,\n"
            "            'peak_rss_mb': 0.0,\n"
            "        },\n"
            "        'cases': [],\n"
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
    benchmark_path = repo_dir / "benchmark.yaml"
    pre_counter = repo_dir / "pre_count.txt"
    benchmark_payload = {
        "schema_version": 1,
        "benchmark_id": "pre-command-test",
        "repo": {"editable_paths": ["solver.py"]},
        "controller": {
            "timeout_seconds": 30,
            "warmup_runs": 1,
            "measured_runs": 2,
            "objective": {
                "primary_metric": "weighted_median_wall_seconds",
                "direction": "minimize",
            },
        },
        "campaign": {"max_iterations": 1, "stop_on_consecutive_rejections": 1},
        "correctness": {"mode": "runner_only"},
        "runtime": {
            "mode": "direct",
            "pre_commands": [
                [
                    sys.executable,
                    "-c",
                    (
                        "from pathlib import Path; "
                        "p = Path('pre_count.txt'); "
                        "n = int(p.read_text(encoding='utf-8')) if p.exists() else 0; "
                        "p.write_text(str(n + 1), encoding='utf-8')"
                    ),
                ]
            ],
            "command": [
                sys.executable,
                "scripts/bench.py",
                "--benchmark",
                "{benchmark}",
                "--emit-json",
            ],
        },
        "cases": [],
    }
    benchmark_path.write_text(
        yaml.safe_dump(
            benchmark_payload,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    loaded_payload = optimize_controller._load_benchmark(benchmark_path)
    run_dir = repo_dir / "runs" / "suite"

    result = optimize_controller._run_benchmark_suite(
        repo_dir,
        benchmark_path=benchmark_path,
        benchmark_payload=loaded_payload,
        run_dir=run_dir,
        timeout_seconds=30,
    )

    assert result["status"] == "ok"
    assert result["correctness_ok"] is True
    assert pre_counter.read_text(encoding="utf-8").strip() == "1"
    assert (run_dir / "pre_command_1.stdout.log").is_file()
    assert (run_dir / "pre_command_1.stderr.log").is_file()
    assert (run_dir / "pre_commands.ok.json").is_file()


def test_run_benchmark_suite_returns_failure_on_runtime_pre_command_crash(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo-pre-command-fail"
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "bench.py").write_text(
        (
            "from __future__ import annotations\n"
            "import argparse\n"
            "import json\n"
            "\n"
            "def main() -> int:\n"
            "    parser = argparse.ArgumentParser()\n"
            "    parser.add_argument('--benchmark', required=True)\n"
            "    parser.add_argument('--emit-json', action='store_true')\n"
            "    parser.parse_args()\n"
            "    print(json.dumps({'benchmark_id': 'pre-command-fail', 'correctness_ok': True, 'summary_metrics': {'weighted_median_wall_seconds': 1.0}, 'cases': []}, sort_keys=True))\n"
            "    return 0\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    raise SystemExit(main())\n"
        ),
        encoding="utf-8",
    )
    benchmark_path = repo_dir / "benchmark.yaml"
    benchmark_payload = {
        "schema_version": 1,
        "benchmark_id": "pre-command-fail",
        "repo": {"editable_paths": ["solver.py"]},
        "controller": {
            "timeout_seconds": 30,
            "warmup_runs": 0,
            "measured_runs": 1,
            "objective": {
                "primary_metric": "weighted_median_wall_seconds",
                "direction": "minimize",
            },
        },
        "campaign": {"max_iterations": 1, "stop_on_consecutive_rejections": 1},
        "correctness": {"mode": "runner_only"},
        "runtime": {
            "mode": "direct",
            "pre_commands": [[sys.executable, "-c", "import sys; sys.exit(3)"]],
            "command": [
                sys.executable,
                "scripts/bench.py",
                "--benchmark",
                "{benchmark}",
                "--emit-json",
            ],
        },
        "cases": [],
    }
    benchmark_path.write_text(
        yaml.safe_dump(
            benchmark_payload,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    loaded_payload = optimize_controller._load_benchmark(benchmark_path)
    run_dir = repo_dir / "runs" / "suite"

    result = optimize_controller._run_benchmark_suite(
        repo_dir,
        benchmark_path=benchmark_path,
        benchmark_payload=loaded_payload,
        run_dir=run_dir,
        timeout_seconds=30,
    )

    assert result["status"] == "crash"
    assert result["ok"] is False
    assert "runtime.pre_commands[1] failed" in str(result.get("reason") or "")
    assert (run_dir / "pre_command_1.stdout.log").is_file()
    assert (run_dir / "pre_command_1.stderr.log").is_file()


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
        "cases": [{"id": "case-1", "converged": True, "outputs": {"force_norm": 1.0}}]
    }
    passing_candidate = {
        "cases": [{"id": "case-1", "converged": True, "outputs": {"force_norm": 1.04}}]
    }
    failing_candidate = {
        "cases": [{"id": "case-1", "converged": True, "outputs": {"force_norm": 1.2}}]
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


def test_compare_correctness_field_tolerances_supports_flat_dotted_case_keys() -> None:
    benchmark_payload = {
        "correctness": {
            "mode": "field_tolerances",
            "field_tolerances": [
                {
                    "field": "thermo.etotal",
                    "relative_delta": 1.0e-6,
                }
            ],
        }
    }
    incumbent_metrics = {
        "cases": [
            {
                "id": "case-1",
                "converged": True,
                "thermo.etotal": -1.2909504,
            }
        ]
    }
    passing_candidate = {
        "cases": [
            {
                "id": "case-1",
                "converged": True,
                "thermo.etotal": -1.2909503,
            }
        ]
    }

    comparison = optimize_controller._compare_correctness(
        benchmark_payload,
        incumbent_metrics=incumbent_metrics,
        candidate_metrics=passing_candidate,
    )

    assert comparison["ok"] is True
    assert comparison["errors"] == []


def test_parse_benchmark_stdout_validates_required_schema() -> None:
    benchmark_payload = {
        "benchmark_id": "mock-contract",
        "controller": {
            "objective": {
                "primary_metric": "weighted_median_wall_seconds",
                "direction": "minimize",
            }
        },
        "cases": [{"id": "case-1"}],
    }
    invalid_output = json.dumps(
        {
            "benchmark_id": "mock-contract",
            "correctness_ok": True,
            "summary_metrics": {"peak_rss_mb": 1.0},
            "cases": [{"id": "case-1", "converged": True}],
        }
    )
    with pytest.raises(cli.PackageError, match="primary metric"):
        optimize_controller._parse_benchmark_stdout(
            invalid_output,
            benchmark_payload=benchmark_payload,
        )


def test_parse_benchmark_stdout_rejects_missing_expected_cases() -> None:
    benchmark_payload = {
        "benchmark_id": "mock-contract",
        "controller": {
            "objective": {
                "primary_metric": "weighted_median_wall_seconds",
                "direction": "minimize",
            }
        },
        "cases": [{"id": "case-1"}, {"id": "case-2"}],
    }
    invalid_output = json.dumps(
        {
            "benchmark_id": "mock-contract",
            "correctness_ok": True,
            "summary_metrics": {"weighted_median_wall_seconds": 1.0},
            "cases": [{"id": "case-1", "converged": True}],
        }
    )
    with pytest.raises(cli.PackageError, match="missing cases"):
        optimize_controller._parse_benchmark_stdout(
            invalid_output,
            benchmark_payload=benchmark_payload,
        )


def test_effective_correctness_benchmark_payload_auto_upgrades_runner_only() -> None:
    benchmark_payload = {
        "correctness": {
            "mode": "runner_only",
            "require_all_cases_converged": True,
        }
    }
    baseline_metrics = {
        "cases": [
            {
                "id": "case-1",
                "converged": True,
                "energy": -10.5,
                "forces": [0.1, 0.2, 0.3],
                "wall_seconds": 1.0,
            }
        ]
    }
    effective, info = optimize_controller._effective_correctness_benchmark_payload(
        benchmark_payload,
        baseline_metrics=baseline_metrics,
    )
    assert info["upgraded"] is True
    assert effective["correctness"]["mode"] == "field_tolerances"
    assert effective["correctness"]["field_tolerances"]
    assert all(
        spec.get("field") in {"energy", "forces"}
        for spec in effective["correctness"]["field_tolerances"]
    )


def test_effective_correctness_benchmark_payload_respects_allow_runner_only() -> None:
    benchmark_payload = {
        "correctness": {
            "mode": "runner_only",
            "allow_runner_only": True,
        }
    }
    baseline_metrics = {"cases": [{"id": "case-1", "converged": True, "energy": -10.5}]}
    effective, info = optimize_controller._effective_correctness_benchmark_payload(
        benchmark_payload,
        baseline_metrics=baseline_metrics,
    )
    assert info["upgraded"] is False
    assert effective["correctness"]["mode"] == "runner_only"


def test_incumbent_relative_primary_metric_normalization_helpers() -> None:
    benchmark_payload = {
        "controller": {
            "objective": {
                "primary_metric": "geomean_wall_ratio_vs_incumbent",
                "direction": "minimize",
                "incumbent_relative_primary": True,
            }
        }
    }
    metrics = {
        "summary_metrics": {
            "geomean_wall_ratio_vs_incumbent": 0.94,
            "weighted_median_wall_seconds": 2.0,
        },
        "cases": [{"id": "case-1"}],
    }
    normalized = optimize_controller._normalize_incumbent_metrics_for_state(
        benchmark_payload,
        primary_metric_name="geomean_wall_ratio_vs_incumbent",
        metrics=metrics,
    )
    assert normalized["summary_metrics"][
        "geomean_wall_ratio_vs_incumbent"
    ] == pytest.approx(1.0)
    assert normalized["summary_metrics"][
        "weighted_median_wall_seconds"
    ] == pytest.approx(2.0)
    context_primary = optimize_controller._objective_primary_for_context(
        benchmark_payload,
        incumbent_metrics=normalized,
        primary_metric_name="geomean_wall_ratio_vs_incumbent",
    )
    assert context_primary == pytest.approx(1.0)


def test_build_optimize_prompt_falls_back_to_nested_incumbent_summary_metrics() -> None:
    benchmark_payload = {
        "benchmark_id": "mock-solver",
        "controller": {
            "objective": {
                "primary_metric": "weighted_median_wall_seconds",
                "direction": "minimize",
            }
        },
    }
    prompt = optimize_prompts.build_optimize_prompt(
        benchmark_payload=benchmark_payload,
        benchmark_rel="scripts/benchmark.yaml",
        program_rel=".fermilink-optimize/program.md",
        controller_memory_rel=".fermilink-optimize/memory.md",
        worker_memory_rel=".fermilink-optimize/worker_memory.md",
        results_rel=".fermilink-optimize/results.tsv",
        recent_results_text="",
        state_payload={
            "incumbent_commit": "abc123",
            "incumbent_metrics": {
                "summary_metrics": {"weighted_median_wall_seconds": 8.0}
            },
        },
        editable_paths=["solver.py"],
    )
    assert "Current incumbent weighted_median_wall_seconds: 8" in prompt
    assert "Sampling profiling with `py-spy`/`perf`/`xctrace`" in prompt


def test_build_optimize_agents_md_mentions_py_spy_for_python_profiling() -> None:
    agents_md = optimize_prompts.build_optimize_agents_md(
        benchmark_rel="scripts/benchmark.yaml",
        program_rel=".fermilink-optimize/program.md",
        controller_memory_rel=".fermilink-optimize/memory.md",
        worker_memory_rel=".fermilink-optimize/worker_memory.md",
        results_rel=".fermilink-optimize/results.tsv",
        editable_paths=["solver.py"],
        immutable_paths=["scripts/**"],
    )
    assert "Sampling profiling with `py-spy`/`perf`/`xctrace`" in agents_md


def test_optimize_quick_mode_plan_only_scaffolds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir, _benchmark_path = _init_optimize_repo(tmp_path)
    prompt_path = repo_dir / "prompt.txt"
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
    _git(repo_dir, "add", "prompt.txt")
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

    parser = cli._build_parser()
    args = parser.parse_args(["optimize", "prompt.txt", "--plan-only"])
    payload = optimize_controller.run_quick_campaign(args)

    assert payload["status"] == "planned"
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
    prompt_path = repo_dir / "prompt.txt"
    prompt_path.write_text(
        ("# Prompt\n" "\n" "```bash\n" "python -c \"print('ok')\"\n" "```\n"),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "prompt.txt")
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

    parser = cli._build_parser()
    args = parser.parse_args(["optimize", "prompt.txt", "--plan-only"])
    payload_first = optimize_controller.run_quick_campaign(args)
    assert payload_first["status"] == "planned"

    benchmark_path = repo_dir / ".fermilink-optimize" / "autogen" / "benchmark.yaml"
    marker = "# user-edit-marker\n"
    benchmark_path.write_text(
        benchmark_path.read_text(encoding="utf-8") + marker,
        encoding="utf-8",
    )

    payload_second = optimize_controller.run_quick_campaign(args)
    assert payload_second["status"] == "planned"
    assert marker in benchmark_path.read_text(encoding="utf-8")


def test_optimize_quick_mode_compiles_skills_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_dir, _benchmark_path = _init_optimize_repo(tmp_path, with_skills=False)
    prompt_path = repo_dir / "prompt.txt"
    prompt_path.write_text(
        ("# Prompt\n" "\n" "```bash\n" "python -c \"print('ok')\"\n" "```\n"),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "prompt.txt")
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

    parser = cli._build_parser()
    args = parser.parse_args(["optimize", "prompt.txt", "--plan-only"])
    payload = optimize_controller.run_quick_campaign(args)

    assert payload["status"] == "planned"
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
    prompt_path = repo_dir / "prompt.txt"
    prompt_path.write_text(
        (
            "# Optimize request\n"
            "\n"
            "Improve force-evaluation throughput while preserving correctness.\n"
        ),
        encoding="utf-8",
    )
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "cpp-lammps-tip4p-force-eval-benchmark.yaml").write_text(
        (
            "schema_version: 1\n"
            "benchmark_id: cpp-template\n"
            "controller:\n"
            "  timeout_seconds: 1200\n"
            "  warmup_runs: 0\n"
            "  measured_runs: 1\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "    direction: minimize\n"
            "    min_relative_improvement: 0.02\n"
            "correctness:\n"
            "  mode: runner_only\n"
            "runtime:\n"
            "  env:\n"
            '    OMP_NUM_THREADS: "1"\n'
            "cases:\n"
            "  - id: tip4p-case\n"
            "    command_preview: lmp -in in.tip4p\n"
        ),
        encoding="utf-8",
    )
    (scripts_dir / "cpp-lammps-tip4p-force-eval-bench.sh").write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\necho ok\n",
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

    parser = cli._build_parser()
    args = parser.parse_args(["optimize", "prompt.txt", "--plan-only"])
    payload = optimize_controller.run_quick_campaign(args)

    assert payload["status"] == "planned"
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
    manifest = json.loads(
        (autogen_root / "quick_mode.json").read_text(encoding="utf-8")
    )
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


def test_optimize_plan_only_supports_git_worktree(tmp_path: Path) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)
    worktree_dir = tmp_path / "repo-worktree"
    _git(
        repo_dir,
        "worktree",
        "add",
        "-b",
        "worktree-optimize-base",
        str(worktree_dir),
        "main",
    )
    benchmark_in_worktree = worktree_dir / benchmark_path.relative_to(repo_dir)

    code = cli.main(
        [
            "optimize",
            "mockpkg",
            str(worktree_dir),
            "--benchmark",
            str(benchmark_in_worktree),
            "--skills-source",
            "existing",
            "--plan-only",
        ]
    )

    assert code == 0
    optimize_root = worktree_dir / ".fermilink-optimize"
    assert (optimize_root / "state.json").exists()
    exclude_raw = _git(worktree_dir, "rev-parse", "--git-path", "info/exclude")
    exclude_path = Path(exclude_raw)
    if not exclude_path.is_absolute():
        exclude_path = (worktree_dir / exclude_path).resolve()
    exclude_lines = exclude_path.read_text(encoding="utf-8").splitlines()
    assert ".fermilink-optimize/" in exclude_lines


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
        _write_solver_in_worker(kwargs, mode="FAST")
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


def test_optimize_state_compacts_raw_runs_for_baseline_and_incumbent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)
    benchmark_payload = yaml.safe_load(benchmark_path.read_text(encoding="utf-8"))
    benchmark_payload["controller"]["measured_runs"] = 2
    benchmark_path.write_text(
        yaml.safe_dump(
            benchmark_payload,
            sort_keys=False,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )
    _git(repo_dir, "add", "scripts/benchmark.yaml")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "set measured runs to 2",
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

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>clear benchmark win</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        _write_solver_in_worker(kwargs, mode="FAST")
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
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 1
    assert "raw_runs" not in state["baseline_metrics"]
    assert "raw_runs" not in state["incumbent_metrics"]


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
        _write_solver_in_worker(kwargs, mode="SLOW")
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
        _write_solver_in_worker(kwargs, mode="BROKEN")
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
        _write_solver_in_worker(kwargs, mode="REGRESS")
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
    assert "performance_regression" in str(
        review_context.get("hard_reject_reason") or ""
    )


def test_optimize_split_hides_test_cases_from_worker_and_evaluates_test_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_split_optimize_repo(tmp_path)

    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(
            provider="codex",
            sandbox_policy="enforce",
            sandbox_mode="workspace-write",
        ),
    )

    seen_prompts: dict[str, str] = {"worker": "", "controller": ""}
    worker_repo_path = {"value": ""}

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        if "controller for a completed FermiLink optimize iteration" in prompt:
            seen_prompts["controller"] = prompt
            return {
                "assistant_text": (
                    "<decision>REJECTED</decision>\n"
                    "<controller_summary>test-only benchmark regressed</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }

        seen_prompts["worker"] = prompt
        worker_repo = Path(str(kwargs.get("repo_dir")))
        worker_repo_path["value"] = str(worker_repo)
        assert not (worker_repo / ".git").exists()
        assert (worker_repo / optimize_git.WORKER_GIT_HIDDEN_BASENAME).exists()
        assert (worker_repo / ".fermilink-optimize" / "benchmark.worker.yaml").is_file()
        assert (worker_repo / ".fermilink-optimize" / "program.md").is_file()
        assert (worker_repo / ".fermilink-optimize" / "memory.md").is_file()
        assert (worker_repo / ".fermilink-optimize" / "results.tsv").is_file()
        for key in optimize_git.WORKER_GIT_ENV_KEYS:
            assert key not in os.environ
        (worker_repo / "solver.py").write_text(
            "MODE = 'TRAIN_FAST'\n", encoding="utf-8"
        )
        return {
            "assistant_text": (
                "<experiment_description>train-only fast path</experiment_description>\n"
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
            "2",
        ]
    )

    assert code == 0
    assert ".fermilink-optimize/benchmark.worker.yaml" in seen_prompts["worker"]
    assert "scripts/benchmark.yaml" not in seen_prompts["worker"]

    worker_benchmark = yaml.safe_load(
        (repo_dir / ".fermilink-optimize" / "benchmark.worker.yaml").read_text(
            encoding="utf-8"
        )
    )
    worker_cases = [
        str(item.get("id") or "")
        for item in worker_benchmark.get("cases", [])
        if isinstance(item, dict)
    ]
    assert worker_cases == ["train-a", "train-b"]

    controller_benchmark = yaml.safe_load(
        (
            repo_dir
            / ".fermilink-optimize"
            / "runs"
            / "iter_0001"
            / "benchmark.controller.yaml"
        ).read_text(encoding="utf-8")
    )
    controller_cases = [
        str(item.get("id") or "")
        for item in controller_benchmark.get("cases", [])
        if isinstance(item, dict)
    ]
    assert controller_cases == ["test-a", "test-b"]

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
    candidate_metrics = review_context.get("candidate_metrics")
    assert isinstance(candidate_metrics, dict)
    case_ids = [
        str(item.get("id") or "")
        for item in candidate_metrics.get("cases", [])
        if isinstance(item, dict)
    ]
    assert case_ids == ["test-a", "test-b"]
    worker_repo = Path(worker_repo_path["value"])
    assert worker_repo_path["value"]
    assert worker_repo.resolve() != repo_dir.resolve()
    assert (worker_repo / ".git").exists()
    assert not (worker_repo / optimize_git.WORKER_GIT_HIDDEN_BASENAME).exists()


def test_optimize_goal_mode_injects_absolute_goal_input_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_split_optimize_repo(tmp_path)

    all_root = optimize_state.goal_inputs_all_root(repo_dir)
    all_root.mkdir(parents=True, exist_ok=True)
    (all_root / "shared.dat").write_text("shared\n", encoding="utf-8")
    optimize_state.ensure_autogen_root(repo_dir)
    optimize_state.write_json_file(
        optimize_state.goal_inputs_manifest_path(repo_dir),
        {
            "schema_version": 1,
            "all_files": ["shared.dat"],
            "shared_files": ["shared.dat"],
            "case_file_map": {},
        },
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

    captured: dict[str, str] = {}

    def fake_run_authoritative_benchmark_suite(*args, **kwargs):
        payload = kwargs.get("benchmark_payload")
        runtime = payload.get("runtime") if isinstance(payload, dict) else {}
        env = runtime.get("env") if isinstance(runtime, dict) else {}
        if isinstance(env, dict):
            captured["controller_goal_input_root"] = str(
                env.get("FERMILINK_GOAL_INPUT_ROOT") or ""
            )
        return {
            "ok": True,
            "status": "ok",
            "correctness_ok": True,
            "summary_metrics": {
                "weighted_median_wall_seconds": 10.0,
                "peak_rss_mb": 32.0,
            },
            "cases": [
                {
                    "id": "test-a",
                    "converged": True,
                    "wall_seconds": 10.0,
                    "error": "",
                },
                {
                    "id": "test-b",
                    "converged": True,
                    "wall_seconds": 10.0,
                    "error": "",
                },
            ],
        }

    monkeypatch.setattr(
        optimize_controller,
        "_run_authoritative_benchmark_suite",
        fake_run_authoritative_benchmark_suite,
    )

    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "optimize",
            "mockpkg",
            str(repo_dir),
            "--benchmark",
            str(benchmark_path),
            "--skills-source",
            "existing",
            "--baseline-only",
        ]
    )
    setattr(args, "_optimize_mode", "goal")

    result = optimize_controller.run_campaign(args)
    assert result.get("status") == "baseline_only"

    expected_controller_root = str(optimize_state.goal_inputs_all_root(repo_dir).resolve())
    expected_worker_root = str(optimize_state.goal_inputs_worker_root(repo_dir).resolve())
    assert captured["controller_goal_input_root"] == expected_controller_root
    assert Path(captured["controller_goal_input_root"]).is_absolute()

    worker_benchmark = yaml.safe_load(
        optimize_state.worker_benchmark_path(repo_dir).read_text(encoding="utf-8")
    )
    worker_runtime = (
        worker_benchmark.get("runtime")
        if isinstance(worker_benchmark, dict)
        else {}
    )
    worker_env = worker_runtime.get("env") if isinstance(worker_runtime, dict) else {}
    assert isinstance(worker_env, dict)
    assert worker_env.get("FERMILINK_GOAL_INPUT_ROOT") == expected_worker_root
    assert Path(str(worker_env.get("FERMILINK_GOAL_INPUT_ROOT") or "")).is_absolute()


def test_optimize_reuses_worker_worktree_across_outer_iterations(
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

    worker_repo_paths: list[str] = []
    worker_turn_count = {"count": 0}

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

        worker_turn_count["count"] += 1
        worker_repo = Path(str(kwargs.get("repo_dir")))
        worker_repo_paths.append(str(worker_repo))
        assert not (worker_repo / ".git").exists()
        assert (worker_repo / optimize_git.WORKER_GIT_HIDDEN_BASENAME).exists()
        _write_solver_in_worker(kwargs, mode=f"FAST_{worker_turn_count['count']}")
        return {
            "assistant_text": (
                f"<experiment_description>fast path {worker_turn_count['count']}</experiment_description>\n"
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
            "2",
            "--stop-on-consecutive-rejections",
            "2",
        ]
    )

    assert code == 0
    assert len(worker_repo_paths) == 2
    assert len(set(worker_repo_paths)) == 1
    worker_repo = Path(worker_repo_paths[0]).resolve()
    assert worker_repo != repo_dir.resolve()
    assert (worker_repo / ".git").exists()
    assert not (worker_repo / optimize_git.WORKER_GIT_HIDDEN_BASENAME).exists()
    state = json.loads(
        (repo_dir / ".fermilink-optimize" / "state.json").read_text(encoding="utf-8")
    )
    assert state["accepted_count"] == 2
    assert state["iteration"] == 2


def test_optimize_worker_prebuild_runs_once_before_first_worker_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)
    benchmark_payload = yaml.safe_load(benchmark_path.read_text(encoding="utf-8"))
    assert isinstance(benchmark_payload, dict)
    runtime = benchmark_payload.get("runtime")
    assert isinstance(runtime, dict)
    pre_command = [sys.executable, "-c", "print('worker-prebuild-probe')"]
    runtime["pre_commands"] = [pre_command]
    benchmark_path.write_text(
        yaml.safe_dump(
            benchmark_payload,
            sort_keys=False,
            default_flow_style=False,
        ),
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

    events: list[tuple[str, str]] = []
    worker_repo_paths: list[str] = []
    real_subprocess_run = subprocess.run

    def _normalize_path_text(raw: str) -> str:
        value = str(raw or "").strip()
        if not value:
            return ""
        return str(Path(value).resolve())

    def wrapped_subprocess_run(*args, **kwargs):
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, list) and command == pre_command:
            events.append(
                ("pre_command", _normalize_path_text(str(kwargs.get("cwd") or "")))
            )
        return real_subprocess_run(*args, **kwargs)

    monkeypatch.setattr(optimize_controller.subprocess, "run", wrapped_subprocess_run)

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

        worker_repo = Path(str(kwargs.get("repo_dir") or "")).resolve()
        worker_repo_paths.append(str(worker_repo))
        events.append(("worker_turn", str(worker_repo)))
        _write_solver_in_worker(kwargs, mode=f"FAST_{len(worker_repo_paths)}")
        return {
            "assistant_text": (
                f"<experiment_description>fast path {len(worker_repo_paths)}</experiment_description>\n"
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
            "--allow-dirty",
            "--max-iterations",
            "2",
            "--stop-on-consecutive-rejections",
            "2",
        ]
    )

    assert code == 0
    assert len(worker_repo_paths) == 2
    assert len(set(worker_repo_paths)) == 1
    worker_repo = worker_repo_paths[0]
    worker_prebuild_indices = [
        index
        for index, event in enumerate(events)
        if event[0] == "pre_command" and event[1] == worker_repo
    ]
    assert len(worker_prebuild_indices) == 1
    worker_turn_indices = [
        index for index, event in enumerate(events) if event[0] == "worker_turn"
    ]
    assert worker_turn_indices
    assert worker_prebuild_indices[0] < worker_turn_indices[0]


def test_optimize_recovers_orphaned_worker_worktree_root(tmp_path: Path) -> None:
    repo_dir, _ = _init_optimize_repo(tmp_path)
    controller_branch = "fermilink-optimize/mockpkg"

    setup = optimize_git.ensure_worker_worktree(
        repo_dir,
        controller_branch=controller_branch,
    )
    worker_root = Path(str(setup.get("worker_root") or "")).resolve()
    assert worker_root.is_dir()
    git_path = worker_root / ".git"
    hidden_path = worker_root / optimize_git.WORKER_GIT_HIDDEN_BASENAME
    assert git_path.exists()

    git_path.rename(hidden_path)
    _git(repo_dir, "worktree", "prune")

    listed_after_prune = _git(repo_dir, "worktree", "list", "--porcelain")
    assert str(worker_root) not in listed_after_prune
    assert hidden_path.exists()

    recovered = optimize_git.ensure_worker_worktree(
        repo_dir,
        controller_branch=controller_branch,
    )

    assert Path(str(recovered.get("worker_root") or "")).resolve() == worker_root
    assert bool(recovered.get("created_worktree")) is True
    assert git_path.exists()
    assert not hidden_path.exists()


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
            _write_solver_in_worker(kwargs, mode="BROKEN")
            return {
                "assistant_text": (
                    "<experiment_description>initial buggy fast path</experiment_description>\n"
                ),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker2")
        _write_solver_in_worker(kwargs, mode="FAST")
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
        repo_dir / ".fermilink-optimize" / "runs" / "iter_0001" / "worker_memory.md"
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
                "assistant_text": (f"submitted\n<pid_number>{proc.pid}</pid_number>\n"),
                "return_code": 0,
                "stderr": "",
            }

        calls.append("worker2")
        _write_solver_in_worker(kwargs, mode="FAST")
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
    ) -> tuple[
        list[str], list[tuple[str, str]], list[tuple[str, str]], dict[str, object]
    ]:
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
                    '{"command": ['
                    f'"{sys.executable}", '
                    '"scripts/mock_submit_bench.py", '
                    '"--benchmark", '
                    '"{benchmark}", '
                    '"--mode", '
                    '"pid"'
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
    ) -> tuple[
        list[str], list[tuple[str, str]], list[tuple[str, str]], dict[str, object]
    ]:
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
                    '{"command": ['
                    f'"{sys.executable}", '
                    '"scripts/mock_submit_bench.py", '
                    '"--benchmark", '
                    '"{benchmark}", '
                    '"--mode", '
                    '"slurm"'
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
                        '{"command": ["definitely_not_a_real_binary_fermilink_test"]}'
                        "</benchmark_launcher>"
                    ),
                    "return_code": 0,
                    "stderr": "",
                }
            return {
                "assistant_text": (
                    "<benchmark_launcher>"
                    '{"command": ['
                    f'"{sys.executable}", '
                    '"scripts/mock_submit_bench.py", '
                    '"--benchmark", '
                    '"{benchmark}", '
                    '"--mode", '
                    '"pid"'
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
        _write_solver_in_worker(kwargs, mode="SLOW")
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
        _write_solver_in_worker(kwargs, mode="FAST")
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

    cleanup_calls: list[tuple[Path, list[str]]] = []

    monkeypatch.setattr(
        optimize_git,
        "list_untracked_paths",
        fake_list_untracked_paths,
    )
    monkeypatch.setattr(
        optimize_git,
        "cleanup_paths",
        lambda repo_path, paths: cleanup_calls.append((Path(repo_path), list(paths))),
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
    controller_cleanup_calls = [
        paths
        for repo_path, paths in cleanup_calls
        if repo_path.resolve() == repo_dir.resolve()
    ]
    assert controller_cleanup_calls == [["new_artifact.tmp"]]


def test_optimize_removes_stale_temporary_agents_before_clean_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir, benchmark_path = _init_optimize_repo(tmp_path)

    (repo_dir / "AGENTS.md").write_text(
        (
            f"{optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER}"
            "temporary optimize instructions\n"
        ),
        encoding="utf-8",
    )
    (repo_dir / "CLAUDE.md").write_text(
        (
            f"{optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER}"
            "temporary optimize alias instructions\n"
        ),
        encoding="utf-8",
    )
    (repo_dir / "GEMINI.md").write_text(
        (
            f"{optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER}"
            "temporary optimize alias instructions\n"
        ),
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
        _write_solver_in_worker(kwargs, mode="FAST")
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
    assert not (repo_dir / "AGENTS.md").exists()
    assert not (repo_dir / "CLAUDE.md").exists()
    assert not (repo_dir / "GEMINI.md").exists()


def test_temporary_optimize_agents_creates_and_cleans_dual_alias_links(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    with optimize_git.temporary_optimize_agents(
        repo_dir,
        provider="codex",
        content="temporary optimize instructions\n",
    ):
        agents_path = repo_dir / "AGENTS.md"
        assert agents_path.is_file()
        assert agents_path.read_text(encoding="utf-8").startswith(
            optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER
        )
        for alias_name in ("CLAUDE.md", "GEMINI.md"):
            alias_path = repo_dir / alias_name
            assert alias_path.is_symlink() or alias_path.is_file()
            assert alias_path.read_text(encoding="utf-8").startswith(
                optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER
            )

    assert not (repo_dir / "AGENTS.md").exists()
    assert not (repo_dir / "CLAUDE.md").exists()
    assert not (repo_dir / "GEMINI.md").exists()


def test_cleanup_stale_temporary_agents_preserves_tracked_agents(
    tmp_path: Path,
) -> None:
    repo_dir, _ = _init_optimize_repo(tmp_path)
    (repo_dir / "AGENTS.md").write_text("project policy\n", encoding="utf-8")
    _git(repo_dir, "add", "AGENTS.md")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "add tracked agents policy",
    )
    (repo_dir / "AGENTS.md").write_text(
        (
            f"{optimize_git.OPTIMIZE_TEMP_AGENTS_HEADER}"
            "stale temporary content should not be deleted when tracked\n"
        ),
        encoding="utf-8",
    )

    removed = optimize_git.cleanup_stale_temporary_optimize_agents(repo_dir)

    assert removed == []
    assert (repo_dir / "AGENTS.md").is_file()
