from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_cli_keeps_only_optimize_parser_and_command_surface_files() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    cli_dir = repo_root / "src" / "fermilink" / "cli"
    optimize_files = sorted(
        str(path.relative_to(cli_dir)).replace("\\", "/")
        for path in cli_dir.rglob("*optimize*.py")
    )

    assert optimize_files == [
        "commands/optimize.py",
        "parser_optimize.py",
    ]


def test_legacy_cli_optimize_modules_are_absent() -> None:
    legacy_modules = [
        "fermilink.cli.optimize_controller",
        "fermilink.cli.optimize_goal",
        "fermilink.cli.optimize_state",
        "fermilink.cli.optimize_git",
        "fermilink.cli.optimize_prompts",
        "fermilink.cli.optimize_source_analysis",
    ]

    for module_name in legacy_modules:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module_name)


def test_optimize_package_facades_expose_primary_entrypoints() -> None:
    campaign = importlib.import_module("fermilink.optimize.campaign")
    benchmark = importlib.import_module("fermilink.optimize.benchmark")
    goal_pipeline = importlib.import_module("fermilink.optimize.goal_pipeline")
    main = importlib.import_module("fermilink.optimize.main")

    assert campaign.run_campaign is main.run_campaign
    assert campaign.run_goal_campaign is main.run_goal_campaign
    assert campaign.run_quick_campaign is main.run_quick_campaign
    assert campaign.read_campaign_status is main.read_campaign_status

    assert benchmark._load_benchmark is main._load_benchmark
    assert benchmark._run_benchmark_suite is main._run_benchmark_suite
    assert benchmark._compare_correctness is main._compare_correctness

    assert goal_pipeline._stage_goal_referenced_inputs is (
        main._stage_goal_referenced_inputs
    )
    assert goal_pipeline._prepare_goal_worker_inputs_subset is (
        main._prepare_goal_worker_inputs_subset
    )
    assert goal_pipeline._validate_goal_benchmark is main._validate_goal_benchmark
    assert goal_pipeline._validate_goal_runner is main._validate_goal_runner
