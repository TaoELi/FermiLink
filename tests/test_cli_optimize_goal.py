"""Tests for goal-driven optimize mode (goal.md parsing, detection, prompts)."""

from __future__ import annotations

import argparse
import contextlib
import json
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli.commands.optimize import _resolve_optimize_mode
from fermilink.optimize import git as optimize_git
from fermilink.optimize import goal as optimize_goal
from fermilink.optimize import main as optimize_controller
from fermilink.optimize import source_analysis as optimize_source_analysis
from fermilink.optimize import state as optimize_state


# ---------------------------------------------------------------------------
# Goal markdown samples
# ---------------------------------------------------------------------------

SAMPLE_GOAL_MD = """\
# Optimization Goal

## Package
pyscf

## Target
Optimize the DIIS extrapolation in pyscf/lib/diis.py for small-molecule
Hartree-Fock SCF convergence speed.

## Editable Scope
- pyscf/scf/**
- pyscf/lib/diis.py

## Performance Metric
Wall-clock time for full SCF convergence (minimize).

## Correctness Constraints
- Total energy must match reference within 5e-8 Hartree
- MO energies must match within 2e-5 RMS
- All cases must converge

## Representative Workloads
- Small closed-shell: H2O/cc-pVDZ RHF
- Small open-shell: NO/cc-pVDZ UHF
- Medium: NH3/cc-pVTZ RHF

## Language
python
"""

SAMPLE_GOAL_FORTRAN = """\
# Optimization Goal

## Package
quantum-espresso

## Target
Optimise the FFT driver in PW/src/fft_parallel.f90 for plane-wave DFT.

## Editable Scope
- PW/src/fft_parallel.f90
- PW/src/fft_helper_subroutines.f90

## Performance Metric
Wall-clock time for a single SCF step (minimize).

## Correctness Constraints
- Total energy must match reference within 1e-6 Ry
- Forces must match within 1e-5 Ry/Bohr

## Representative Workloads
- Si bulk (2 atoms) PBE scf
- Al bulk (4 atoms) PBE scf
"""

SAMPLE_GOAL_CPP = """\
# Optimization Goal

## Package
lammps

## Target
Optimise the TIP4P force evaluation for water simulations.

## Editable Scope
- src/MOLECULE/pair_lj_cut_tip4p_long.cpp

## Correctness
- Total energy must match within 1e-10
- Force components must match within 1e-8
"""

MINIMAL_GOAL = """\
## Package
mypackage

## Target
Improve solver performance.
"""

NOT_A_GOAL = """\
# Quick optimize prompt

Please optimize the solver for speed.
Run `python -m pytest` to verify.
"""


# ---------------------------------------------------------------------------
# Goal detection
# ---------------------------------------------------------------------------


class TestIsGoalMarkdown:
    def test_full_goal_detected(self) -> None:
        assert optimize_goal.is_goal_markdown(SAMPLE_GOAL_MD)

    def test_fortran_goal_detected(self) -> None:
        assert optimize_goal.is_goal_markdown(SAMPLE_GOAL_FORTRAN)

    def test_cpp_goal_detected(self) -> None:
        assert optimize_goal.is_goal_markdown(SAMPLE_GOAL_CPP)

    def test_minimal_goal_detected(self) -> None:
        assert optimize_goal.is_goal_markdown(MINIMAL_GOAL)

    def test_plain_prompt_not_detected(self) -> None:
        assert not optimize_goal.is_goal_markdown(NOT_A_GOAL)

    def test_empty_not_detected(self) -> None:
        assert not optimize_goal.is_goal_markdown("")


# ---------------------------------------------------------------------------
# Goal parsing
# ---------------------------------------------------------------------------


class TestParseGoal:
    def test_full_goal_parse(self) -> None:
        spec = optimize_goal.parse_goal(SAMPLE_GOAL_MD)
        assert spec["package"] == "pyscf"
        assert "DIIS" in spec["target"]
        assert "pyscf/scf/**" in spec["editable_scope"]
        assert "pyscf/lib/diis.py" in spec["editable_scope"]
        assert len(spec["correctness_constraints"]) == 3
        assert len(spec["workloads"]) == 3
        assert spec["language"] == "python"
        assert spec["raw_text"] == SAMPLE_GOAL_MD

    def test_fortran_goal_parse(self) -> None:
        spec = optimize_goal.parse_goal(SAMPLE_GOAL_FORTRAN)
        assert spec["package"] == "quantum-espresso"
        assert "fortran" not in spec["language"].lower()  # not explicitly set
        assert len(spec["editable_scope"]) == 2
        assert len(spec["workloads"]) == 2
        assert len(spec["correctness_constraints"]) == 2

    def test_cpp_goal_parse(self) -> None:
        spec = optimize_goal.parse_goal(SAMPLE_GOAL_CPP)
        assert spec["package"] == "lammps"
        assert "TIP4P" in spec["target"]
        assert len(spec["editable_scope"]) == 1
        # Correctness constraints come from the "correctness" section
        assert len(spec["correctness_constraints"]) == 2

    def test_minimal_goal_parse(self) -> None:
        spec = optimize_goal.parse_goal(MINIMAL_GOAL)
        assert spec["package"] == "mypackage"
        assert "solver" in spec["target"].lower()
        assert spec["editable_scope"] == []
        assert spec["workloads"] == []
        assert spec["language"] == ""

    def test_missing_sections_default_empty(self) -> None:
        spec = optimize_goal.parse_goal("## Package\nfoo\n")
        assert spec["package"] == "foo"
        assert spec["target"] == ""
        assert spec["editable_scope"] == []
        assert spec["performance_metric"] == ""
        assert spec["correctness_constraints"] == []
        assert spec["workloads"] == []

    def test_build_commands_from_code_blocks(self) -> None:
        text = (
            "## Package\nfoo\n## Target\nbar\n"
            "## Build\n"
            "```\npip install -e .\n```\n"
            "```\nmake install\n```\n"
        )
        spec = optimize_goal.parse_goal(text)
        assert len(spec["build_commands"]) == 2
        assert "pip install" in spec["build_commands"][0]


# ---------------------------------------------------------------------------
# Source analysis extraction
# ---------------------------------------------------------------------------


class TestExtractionHelpers:
    def test_extract_source_analysis(self) -> None:
        analysis = {
            "package": "pyscf",
            "language": "python",
            "entry_points": [{"name": "kernel", "module_or_file": "pyscf/scf/hf.py"}],
        }
        text = f"Some preamble\n<source_analysis>\n{json.dumps(analysis)}\n</source_analysis>\nMore text"
        extracted = optimize_source_analysis.extract_source_analysis(text)
        assert extracted is not None
        assert extracted["package"] == "pyscf"
        assert len(extracted["entry_points"]) == 1

    def test_extract_source_analysis_invalid_json(self) -> None:
        text = "<source_analysis>not json</source_analysis>"
        assert optimize_source_analysis.extract_source_analysis(text) is None

    def test_extract_source_analysis_missing(self) -> None:
        assert optimize_source_analysis.extract_source_analysis("no tag here") is None

    def test_extract_benchmark_yaml(self) -> None:
        yaml_text = "schema_version: 1\nbenchmark_id: test\n"
        text = f"<benchmark_yaml>\n{yaml_text}\n</benchmark_yaml>"
        extracted = optimize_source_analysis.extract_benchmark_yaml(text)
        assert extracted is not None
        assert "schema_version" in extracted

    def test_extract_runner_script(self) -> None:
        script = "#!/usr/bin/env python3\nimport sys\n"
        text = f"<runner_script>\n{script}\n</runner_script>"
        extracted = optimize_source_analysis.extract_runner_script(text)
        assert extracted is not None
        assert "import sys" in extracted

    def test_extract_analysis_summary(self) -> None:
        text = (
            "<analysis_summary>Found 3 entry points and 5 test cases</analysis_summary>"
        )
        assert optimize_source_analysis.extract_analysis_summary(text) == (
            "Found 3 entry points and 5 test cases"
        )

    def test_extract_review_notes(self) -> None:
        text = "<review_notes>Check tolerance for S2</review_notes>"
        assert optimize_source_analysis.extract_review_notes(text) == (
            "Check tolerance for S2"
        )


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


class TestPromptConstruction:
    def test_source_analysis_agents_md(self) -> None:
        agents = optimize_source_analysis.build_source_analysis_agents_md(
            goal_rel="goal.md",
            autogen_rel=".fermilink-optimize/autogen",
        )
        assert "goal.md" in agents
        assert ".fermilink-optimize/autogen" in agents
        assert "Do not modify any source code" in agents

    def test_benchmark_generation_agents_md(self) -> None:
        agents = optimize_source_analysis.build_benchmark_generation_agents_md(
            goal_rel="goal.md",
            analysis_rel=".fermilink-optimize/autogen/goal_analysis.json",
            autogen_rel=".fermilink-optimize/autogen",
        )
        assert "goal.md" in agents
        assert "goal_analysis.json" in agents

    def test_source_analysis_prompt_contains_goal(self) -> None:
        spec = optimize_goal.parse_goal(SAMPLE_GOAL_MD)
        prompt = optimize_source_analysis.build_source_analysis_prompt(
            goal_spec=spec,
            goal_rel="goal.md",
            language="python",
            tracked_file_summary="  src/ (42 files)",
        )
        assert "pyscf" in prompt
        assert "DIIS" in prompt
        assert "source_analysis" in prompt
        assert "DONE" in prompt

    def test_benchmark_generation_prompt_contains_analysis(self) -> None:
        spec = optimize_goal.parse_goal(SAMPLE_GOAL_MD)
        analysis = {"package": "pyscf", "entry_points": []}
        prompt = optimize_source_analysis.build_benchmark_generation_prompt(
            goal_spec=spec,
            goal_rel="goal.md",
            analysis=analysis,
            analysis_rel=".fermilink-optimize/autogen/goal_analysis.json",
            language="python",
            runner_template="# runner template",
            benchmark_template="# benchmark template",
            autogen_benchmark_rel=".fermilink-optimize/autogen/benchmark.yaml",
            autogen_runner_rel=".fermilink-optimize/autogen/benchmark_runner.py",
            controller_timeout_seconds=7200,
        )
        assert "pyscf" in prompt
        assert "benchmark_yaml" in prompt
        assert "runner_script" in prompt
        assert "`timeout_seconds: 7200`" in prompt
        assert "runner_only" in prompt or "field_tolerances" in prompt
        assert "MUST be a non-empty list" in prompt
        assert "Never emit an empty `field_tolerances` list." in prompt
        assert "FERMILINK_GOAL_INPUT_ROOT" in prompt
        assert "run from the resolved input-root" in prompt
        assert (
            "Do not infer input roots from fixed benchmark-path parent depth." in prompt
        )
        assert "hard-coded `..` parent-depth assumptions" in prompt
        assert "`goal_context`" in prompt
        assert "`target`" in prompt
        assert "`initial_hypothesis`" in prompt
        assert "`intent_level`" in prompt
        assert "set to `guidance`" in prompt

    def test_benchmark_generation_prompt_requires_pre_commands_when_build_commands_exist(
        self,
    ) -> None:
        build_goal = (
            "# Optimization Goal\n\n"
            "## Package\n"
            "pyscf\n\n"
            "## Language\n"
            "python\n\n"
            "## Target\n"
            "Tune SCF setup/runtime path.\n\n"
            "## Editable Scope\n"
            "- pyscf/scf/diis.py\n\n"
            "## Build\n"
            "```bash\n"
            "python -m pip install -e .\n"
            "```\n"
        )
        spec = optimize_goal.parse_goal(build_goal)
        prompt = optimize_source_analysis.build_benchmark_generation_prompt(
            goal_spec=spec,
            goal_rel="goal.md",
            analysis={"package": "pyscf", "entry_points": []},
            analysis_rel=".fermilink-optimize/autogen/goal_analysis.json",
            language="python",
            runner_template="# runner template",
            benchmark_template="# benchmark template",
            autogen_benchmark_rel=".fermilink-optimize/autogen/benchmark.yaml",
            autogen_runner_rel=".fermilink-optimize/autogen/benchmark_runner.py",
        )
        assert "runtime.pre_commands" in prompt
        assert "REQUIRED for this goal" in prompt
        assert "bash', '-lc'" in prompt

    def test_benchmark_generation_prompt_requires_explicit_python_path_for_pinned_env(
        self,
    ) -> None:
        build_goal = (
            "# Optimization Goal\n\n"
            "## Package\n"
            "pyscf\n\n"
            "## Language\n"
            "python\n\n"
            "## Target\n"
            "Tune SCF setup/runtime path.\n\n"
            "## Editable Scope\n"
            "- pyscf/scf/diis.py\n\n"
            "## Build\n"
            "```bash\n"
            "export VENV=/shared/venvs/pyscf-diis\n"
            "source \"$VENV/bin/activate\"\n"
            "python -m pip install -e .\n"
            "```\n"
        )
        spec = optimize_goal.parse_goal(build_goal)
        prompt = optimize_source_analysis.build_benchmark_generation_prompt(
            goal_spec=spec,
            goal_rel="goal.md",
            analysis={"package": "pyscf", "entry_points": []},
            analysis_rel=".fermilink-optimize/autogen/goal_analysis.json",
            language="python",
            runner_template="# runner template",
            benchmark_template="# benchmark template",
            autogen_benchmark_rel=".fermilink-optimize/autogen/benchmark.yaml",
            autogen_runner_rel=".fermilink-optimize/autogen/benchmark_runner.py",
        )
        assert "pins a specific venv/conda" in prompt
        assert "Do not rely on ambient system `python` lookups." in prompt
        assert "explicit interpreter path in `runtime.command`" in prompt
        assert (
            "in `benchmark_runner.py` use the same explicit path for any Python"
            in prompt
        )
        assert "subprocesses instead of bare `python`/PATH resolution." in prompt


# ---------------------------------------------------------------------------
# State paths
# ---------------------------------------------------------------------------


class TestGoalStatePaths:
    def test_goal_paths_under_autogen(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        analysis = optimize_state.goal_analysis_path(root)
        benchmark = optimize_state.goal_benchmark_path(root)
        runner = optimize_state.goal_runner_path(root)
        manifest = optimize_state.goal_manifest_path(root)
        assert str(analysis).endswith("goal_analysis.json")
        assert str(benchmark).endswith("benchmark.yaml")
        assert str(runner).endswith("benchmark_runner.py")
        assert str(manifest).endswith("goal_mode.json")
        # All under autogen
        autogen = str(optimize_state.autogen_root(root))
        assert str(analysis).startswith(autogen)
        assert str(benchmark).startswith(autogen)
        assert str(runner).startswith(autogen)
        assert str(manifest).startswith(autogen)


# ---------------------------------------------------------------------------
# Goal input staging
# ---------------------------------------------------------------------------


class TestGoalInputStaging:
    def test_stage_goal_referenced_inputs_copies_external_workload_files(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_root = tmp_path / "goal_bundle"
        goal_root.mkdir(parents=True, exist_ok=True)

        goal_path = goal_root / "goal.md"
        goal_path.write_text(MINIMAL_GOAL, encoding="utf-8")
        (goal_root / "in.tip4p_nve").write_text("run 100\n", encoding="utf-8")
        (goal_root / "water_216_data.lmp").write_text("atoms\n", encoding="utf-8")
        (goal_root / "sub").mkdir(parents=True, exist_ok=True)
        (goal_root / "sub" / "settings.inc").write_text(
            "pair_style\n", encoding="utf-8"
        )

        staged = optimize_controller._stage_goal_referenced_inputs(
            repo_root,
            goal_path=goal_path,
            goal_spec={
                "raw_text": MINIMAL_GOAL,
                "workloads": [
                    "train-small: `lmp -in in.tip4p_nve -var data water_216_data.lmp`",
                    'train-medium: "--config sub/settings.inc"',
                    "train-large: missing/data.missing",
                ],
            },
        )

        all_root = optimize_state.goal_inputs_all_root(repo_root)
        assert staged["all_root"] == all_root
        assert (all_root / "in.tip4p_nve").is_file()
        assert (all_root / "water_216_data.lmp").is_file()
        assert (all_root / "sub" / "settings.inc").is_file()
        assert set(staged["all_files"]) == {
            "in.tip4p_nve",
            "water_216_data.lmp",
            "sub/settings.inc",
        }
        assert staged["case_file_map"] == {
            "train-small": ["in.tip4p_nve", "water_216_data.lmp"],
            "train-medium": ["sub/settings.inc"],
        }
        missing = staged["missing_references"]
        assert isinstance(missing, list)
        assert len(missing) == 1
        assert missing[0]["case_id"] == "train-large"
        assert missing[0]["reference"] == "missing/data.missing"
        manifest = json.loads(
            optimize_state.goal_inputs_manifest_path(repo_root).read_text(
                encoding="utf-8"
            )
        )
        assert manifest["all_root_rel"] == ".fermilink-optimize/inputs/all"
        assert manifest["worker_root_rel"] == ".fermilink-optimize/inputs/worker"

    def test_prepare_goal_worker_inputs_subset_uses_train_cases_with_split(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        all_root = optimize_state.goal_inputs_all_root(repo_root)
        all_root.mkdir(parents=True, exist_ok=True)
        (all_root / "shared").mkdir(parents=True, exist_ok=True)
        (all_root / "train").mkdir(parents=True, exist_ok=True)
        (all_root / "test").mkdir(parents=True, exist_ok=True)
        (all_root / "shared" / "common.in").write_text("common\n", encoding="utf-8")
        (all_root / "train" / "train-a.in").write_text("train\n", encoding="utf-8")
        (all_root / "test" / "test-a.in").write_text("test\n", encoding="utf-8")

        optimize_state.ensure_autogen_root(repo_root)
        optimize_state.write_json_file(
            optimize_state.goal_inputs_manifest_path(repo_root),
            {
                "schema_version": 1,
                "all_files": [
                    "shared/common.in",
                    "train/train-a.in",
                    "test/test-a.in",
                ],
                "shared_files": ["shared/common.in"],
                "case_file_map": {
                    "train-a": ["train/train-a.in"],
                    "test-a": ["test/test-a.in"],
                },
            },
        )

        subset = optimize_controller._prepare_goal_worker_inputs_subset(
            repo_root,
            split_enabled=True,
            train_case_ids=["train-a"],
        )

        worker_root = optimize_state.goal_inputs_worker_root(repo_root)
        assert subset["enabled"] is True
        assert subset["fallback_reason"] == ""
        assert set(subset["worker_files"]) == {"shared/common.in", "train/train-a.in"}
        assert (worker_root / "shared" / "common.in").is_file()
        assert (worker_root / "train" / "train-a.in").is_file()
        assert not (worker_root / "test" / "test-a.in").exists()

    def test_prepare_goal_worker_inputs_subset_falls_back_when_train_unmapped(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        all_root = optimize_state.goal_inputs_all_root(repo_root)
        all_root.mkdir(parents=True, exist_ok=True)
        (all_root / "shared").mkdir(parents=True, exist_ok=True)
        (all_root / "test").mkdir(parents=True, exist_ok=True)
        (all_root / "shared" / "common.in").write_text("common\n", encoding="utf-8")
        (all_root / "test" / "test-a.in").write_text("test\n", encoding="utf-8")

        optimize_state.ensure_autogen_root(repo_root)
        optimize_state.write_json_file(
            optimize_state.goal_inputs_manifest_path(repo_root),
            {
                "schema_version": 1,
                "all_files": ["shared/common.in", "test/test-a.in"],
                "shared_files": ["shared/common.in"],
                "case_file_map": {
                    "test-a": ["test/test-a.in"],
                },
            },
        )

        subset = optimize_controller._prepare_goal_worker_inputs_subset(
            repo_root,
            split_enabled=True,
            train_case_ids=["train-a"],
        )

        worker_root = optimize_state.goal_inputs_worker_root(repo_root)
        assert subset["enabled"] is True
        assert (
            subset["fallback_reason"]
            == "split_case_ids_not_mapped_in_goal_inputs_manifest"
        )
        assert set(subset["worker_files"]) == {"shared/common.in", "test/test-a.in"}
        assert (worker_root / "shared" / "common.in").is_file()
        assert (worker_root / "test" / "test-a.in").is_file()

    def test_stage_goal_referenced_inputs_tolerates_tilde_nonpath_tokens(
        self, tmp_path: Path
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_root = tmp_path / "goal_bundle"
        goal_root.mkdir(parents=True, exist_ok=True)

        goal_path = goal_root / "goal.md"
        goal_path.write_text(MINIMAL_GOAL, encoding="utf-8")
        (goal_root / "in.tip4p_nve").write_text("run 100\n", encoding="utf-8")

        staged = optimize_controller._stage_goal_referenced_inputs(
            repo_root,
            goal_path=goal_path,
            goal_spec={
                "raw_text": MINIMAL_GOAL,
                "workloads": [
                    "train-small: ~1000-steps use in.tip4p_nve",
                    "train-large: ~nonexistent_user/input.lmp",
                ],
            },
        )

        assert "in.tip4p_nve" in set(staged["all_files"])
        missing_refs = {
            str(item.get("reference") or "") for item in staged["missing_references"]
        }
        assert "~nonexistent_user/input.lmp" in missing_refs


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


class TestModeResolution:
    def test_goal_flag_forces_goal_mode(self, tmp_path: Path) -> None:
        goal_file = tmp_path / "goal.md"
        goal_file.write_text(SAMPLE_GOAL_MD, encoding="utf-8")
        args = argparse.Namespace(
            package_id=str(goal_file),
            project_path=None,
            benchmark=None,
            goal=True,
        )
        assert _resolve_optimize_mode(args) == "goal"

    def test_goal_autodetect_from_content(self, tmp_path: Path) -> None:
        goal_file = tmp_path / "optimize.md"
        goal_file.write_text(SAMPLE_GOAL_MD, encoding="utf-8")
        args = argparse.Namespace(
            package_id=str(goal_file),
            project_path=None,
            benchmark=None,
            goal=False,
        )
        assert _resolve_optimize_mode(args) == "goal"

    def test_plain_markdown_defaults_to_goal(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text(NOT_A_GOAL, encoding="utf-8")
        args = argparse.Namespace(
            package_id=str(prompt_file),
            project_path=None,
            benchmark=None,
            goal=False,
        )
        assert _resolve_optimize_mode(args) == "goal"

    def test_plain_text_prompt_rejected(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text(NOT_A_GOAL, encoding="utf-8")
        args = argparse.Namespace(
            package_id=str(prompt_file),
            project_path=None,
            benchmark=None,
            goal=False,
        )
        with pytest.raises(cli.PackageError):
            _resolve_optimize_mode(args)

    def test_parser_accepts_goal_flag(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["optimize", "goal.md", "--goal"])
        assert args.goal is True


# ---------------------------------------------------------------------------
# Goal resume behavior
# ---------------------------------------------------------------------------


class TestGoalResume:
    def test_goal_resume_reuses_autogen_benchmark_and_skips_generation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_path = repo_root / "goal.md"
        goal_path.write_text(MINIMAL_GOAL, encoding="utf-8")

        autogen_root = optimize_state.ensure_autogen_root(repo_root)
        benchmark_path = autogen_root / optimize_state.GOAL_BENCHMARK_FILENAME
        runner_path = autogen_root / optimize_state.GOAL_RUNNER_FILENAME
        runner_path.write_text("import argparse\n", encoding="utf-8")
        benchmark_path.write_text(
            (
                "schema_version: 1\n"
                "benchmark_id: goal-resume\n"
                "repo:\n"
                "  editable_paths:\n"
                "    - solver.py\n"
                "controller:\n"
                "  objective:\n"
                "    primary_metric: weighted_median_wall_seconds\n"
                "    direction: minimize\n"
                "correctness:\n"
                "  mode: runner_only\n"
                "runtime:\n"
                "  mode: direct\n"
                "  command:\n"
                "    - python\n"
                "    - .fermilink-optimize/autogen/benchmark_runner.py\n"
                "    - --benchmark\n"
                '    - "{benchmark}"\n'
                "    - --emit-json\n"
            ),
            encoding="utf-8",
        )

        monkeypatch.chdir(repo_root)
        monkeypatch.setattr(cli, "_ensure_compile_repo_ready", lambda _repo: False)
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_analysis_turn",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("source analysis should be skipped on goal resume")
            ),
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_generation_turn",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("benchmark generation should be skipped on goal resume")
            ),
        )
        captured: dict[str, str] = {}

        def fake_run_campaign(campaign_args: argparse.Namespace) -> dict[str, object]:
            captured["package_id"] = str(getattr(campaign_args, "package_id", ""))
            captured["project_path"] = str(getattr(campaign_args, "project_path", ""))
            captured["benchmark"] = str(getattr(campaign_args, "benchmark", ""))
            return {"status": "completed"}

        monkeypatch.setattr(optimize_controller, "run_campaign", fake_run_campaign)

        args = argparse.Namespace(
            package_id="goal.md",
            hpc_profile=None,
            sandbox=None,
            skills_source="existing",
            resume=True,
        )
        payload = optimize_controller.run_goal_campaign(args)

        assert captured["package_id"] == "mypackage"
        assert captured["project_path"] == str(repo_root)
        assert captured["benchmark"] == str(benchmark_path)
        assert payload["goal_mode"] is True
        assert payload["goal_resume"] is True
        assert payload["scaffold_benchmark_path"] == str(benchmark_path)

    def test_goal_resume_falls_back_to_state_benchmark_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_path = repo_root / "goal.md"
        goal_path.write_text(MINIMAL_GOAL, encoding="utf-8")

        optimize_state.ensure_optimize_root(repo_root)
        autogen_root = optimize_state.ensure_autogen_root(repo_root)
        runner_path = autogen_root / optimize_state.GOAL_RUNNER_FILENAME
        runner_path.write_text("import argparse\n", encoding="utf-8")
        fallback_benchmark_path = repo_root / "benchmark.resume.yaml"
        fallback_benchmark_path.write_text(
            (
                "schema_version: 1\n"
                "benchmark_id: goal-resume\n"
                "repo:\n"
                "  editable_paths:\n"
                "    - solver.py\n"
                "controller:\n"
                "  objective:\n"
                "    primary_metric: weighted_median_wall_seconds\n"
                "    direction: minimize\n"
                "correctness:\n"
                "  mode: runner_only\n"
                "runtime:\n"
                "  mode: direct\n"
                "  command:\n"
                "    - python\n"
                "    - .fermilink-optimize/autogen/benchmark_runner.py\n"
                "    - --benchmark\n"
                '    - "{benchmark}"\n'
                "    - --emit-json\n"
            ),
            encoding="utf-8",
        )
        optimize_state.write_state(
            optimize_state.state_path(repo_root),
            {
                "benchmark_path": "benchmark.resume.yaml",
            },
        )

        monkeypatch.chdir(repo_root)
        monkeypatch.setattr(cli, "_ensure_compile_repo_ready", lambda _repo: False)
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_analysis_turn",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("source analysis should be skipped on goal resume")
            ),
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_generation_turn",
            lambda **_kwargs: (_ for _ in ()).throw(
                AssertionError("benchmark generation should be skipped on goal resume")
            ),
        )
        captured: dict[str, str] = {}

        def fake_run_campaign(campaign_args: argparse.Namespace) -> dict[str, object]:
            captured["benchmark"] = str(getattr(campaign_args, "benchmark", ""))
            return {"status": "completed"}

        monkeypatch.setattr(optimize_controller, "run_campaign", fake_run_campaign)

        args = argparse.Namespace(
            package_id="goal.md",
            hpc_profile=None,
            sandbox=None,
            skills_source="existing",
            resume=True,
        )
        payload = optimize_controller.run_goal_campaign(args)

        assert captured["benchmark"] == str(fallback_benchmark_path)
        assert payload["goal_resume"] is True


# ---------------------------------------------------------------------------
# Goal preflight behavior
# ---------------------------------------------------------------------------


class TestGoalPreflight:
    def test_preflight_issue_lines_include_case_errors_and_paths(
        self, tmp_path: Path
    ) -> None:
        project_root = tmp_path / "repo"
        project_root.mkdir(parents=True, exist_ok=True)
        run_dir = project_root / ".fermilink-optimize" / "runs" / "goal_preflight_00"
        run_dir.mkdir(parents=True, exist_ok=True)
        benchmark_path = run_dir / "benchmark.preflight.yaml"
        benchmark_path.write_text("schema_version: 1\n", encoding="utf-8")
        metrics_path = run_dir / "metrics.json"
        metrics_path.write_text("{}", encoding="utf-8")
        stdout_path = run_dir / "measured_1.stdout.log"
        stderr_path = run_dir / "measured_1.stderr.log"
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("fatal\n", encoding="utf-8")

        issues = optimize_controller._goal_preflight_issue_lines(
            project_root,
            preflight_result={
                "status": "ok",
                "correctness_ok": False,
                "cases": [
                    {
                        "id": "test-b",
                        "converged": False,
                        "run_success_flag": 0,
                        "error": "dump custom requires explicit fields like id fx fy fz",
                    }
                ],
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            },
            run_dir=run_dir,
            benchmark_path=benchmark_path,
        )

        assert any("correctness_ok" in item for item in issues)
        assert any("Case `test-b`" in item for item in issues)
        assert any("id fx fy fz" in item for item in issues)
        assert any(
            ".fermilink-optimize/runs/goal_preflight_00/metrics.json" in item
            for item in issues
        )
        assert any(
            ".fermilink-optimize/runs/goal_preflight_00/measured_1.stderr.log" in item
            for item in issues
        )

    def test_run_goal_campaign_repairs_after_preflight_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_path = repo_root / "goal.md"
        goal_path.write_text(
            (
                "# Optimization Goal\n\n"
                "## Package\n"
                "mypackage\n\n"
                "## Target\n"
                "Improve solver performance.\n\n"
                "## Representative Workloads\n"
                "- train-a: `input.dat`\n"
                "- test-b: `input.dat`\n"
            ),
            encoding="utf-8",
        )
        (repo_root / "solver.py").write_text("MODE = 'BASELINE'\n", encoding="utf-8")
        (repo_root / "input.dat").write_text("payload\n", encoding="utf-8")

        benchmark_yaml = (
            "schema_version: 1\n"
            "benchmark_id: goal-preflight\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - solver.py\n"
            "controller:\n"
            "  timeout_seconds: 30\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "    direction: minimize\n"
            "correctness:\n"
            "  mode: runner_only\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - .fermilink-optimize/autogen/benchmark_runner.py\n"
            "    - --benchmark\n"
            '    - "{benchmark}"\n'
            "    - --emit-json\n"
            "cases:\n"
            "  - id: train-a\n"
            "    weight: 1.0\n"
            "  - id: test-b\n"
            "    weight: 1.0\n"
            "split:\n"
            "  train_case_ids:\n"
            "    - train-a\n"
        )
        runner_script = "import argparse\n"
        analysis_payload = {
            "package": "mypackage",
            "language": "python",
            "entry_points": [],
            "editable_paths": ["solver.py"],
            "immutable_paths": [".fermilink-optimize/**", "skills/**"],
        }

        repair_prompts: list[str] = []
        preflight_calls = {"count": 0}
        campaign_capture: dict[str, str] = {}

        monkeypatch.chdir(repo_root)
        monkeypatch.setattr(cli, "_ensure_compile_repo_ready", lambda _repo: False)
        monkeypatch.setattr(
            cli,
            "resolve_agent_runtime_policy",
            lambda: argparse.Namespace(
                provider="codex",
                sandbox_policy="enforce",
                sandbox_mode="workspace-write",
                model=None,
                reasoning_effort=None,
            ),
        )
        monkeypatch.setattr(
            optimize_git,
            "temporary_optimize_agents",
            lambda *args, **kwargs: contextlib.nullcontext(),
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_analysis_turn",
            lambda **_kwargs: {
                "assistant_text": (
                    f"<source_analysis>\n{json.dumps(analysis_payload)}\n</source_analysis>\n"
                    "<analysis_summary>ok</analysis_summary>\n"
                )
            },
        )
        monkeypatch.setattr(
            optimize_controller,
            "_collect_tracked_files",
            lambda _project_root: ["goal.md", "input.dat", "solver.py"],
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_generation_turn",
            lambda **_kwargs: {
                "assistant_text": (
                    f"<benchmark_yaml>\n{benchmark_yaml}\n</benchmark_yaml>\n"
                    f"<runner_script>\n{runner_script}\n</runner_script>\n"
                )
            },
        )

        def fake_exec_chat_turn(**kwargs):
            repair_prompts.append(str(kwargs.get("prompt") or ""))
            return {
                "assistant_text": (
                    f"<benchmark_yaml>\n{benchmark_yaml}\n</benchmark_yaml>\n"
                    f"<runner_script>\n{runner_script}\n</runner_script>\n"
                )
            }

        monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_exec_chat_turn)

        def fake_run_benchmark_suite(
            project_root: Path,
            *,
            benchmark_path: Path,
            benchmark_payload: dict[str, object],
            run_dir: Path,
            timeout_seconds: int,
            runtime_override: dict[str, object] | None = None,
        ) -> dict[str, object]:
            del project_root, benchmark_path, timeout_seconds, runtime_override
            preflight_calls["count"] += 1
            runtime = (
                benchmark_payload.get("runtime")
                if isinstance(benchmark_payload, dict)
                else {}
            )
            env = runtime.get("env") if isinstance(runtime, dict) else {}
            assert isinstance(env, dict)
            assert env.get("FERMILINK_GOAL_INPUT_ROOT") == str(
                optimize_state.goal_inputs_all_root(repo_root).resolve()
            )
            cases = (
                benchmark_payload.get("cases")
                if isinstance(benchmark_payload, dict)
                else []
            )
            case_ids = [
                str(item.get("id") or "") for item in cases if isinstance(item, dict)
            ]
            assert case_ids == ["test-b"]
            if preflight_calls["count"] == 1:
                run_dir.mkdir(parents=True, exist_ok=True)
                stdout_path = run_dir / "measured_1.stdout.log"
                stderr_path = run_dir / "measured_1.stderr.log"
                stdout_path.write_text("", encoding="utf-8")
                stderr_path.write_text(
                    "Command exited with non-zero status 1\n", encoding="utf-8"
                )
                return {
                    "ok": False,
                    "status": "crash",
                    "reason": "Command exited with non-zero status 1",
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                }
            return {
                "status": "ok",
                "correctness_ok": True,
                "summary_metrics": {
                    "weighted_median_wall_seconds": 1.0,
                    "peak_rss_mb": 8.0,
                },
                "cases": [
                    {
                        "id": "test-b",
                        "converged": True,
                        "error": "",
                    }
                ],
            }

        monkeypatch.setattr(
            optimize_controller,
            "_run_benchmark_suite",
            fake_run_benchmark_suite,
        )

        def fake_run_campaign(campaign_args: argparse.Namespace) -> dict[str, object]:
            campaign_capture["benchmark"] = str(getattr(campaign_args, "benchmark", ""))
            return {"status": "completed"}

        monkeypatch.setattr(optimize_controller, "run_campaign", fake_run_campaign)

        args = argparse.Namespace(
            package_id="goal.md",
            hpc_profile=None,
            sandbox=None,
            skills_source="existing",
            resume=False,
        )
        payload = optimize_controller.run_goal_campaign(args)

        assert payload["goal_mode"] is True
        assert campaign_capture["benchmark"] == str(
            optimize_state.goal_benchmark_path(repo_root)
        )
        assert preflight_calls["count"] == 2
        assert len(repair_prompts) == 1
        assert (
            "Generated benchmark preflight failed with status `crash`."
            in repair_prompts[0]
        )
        assert (
            ".fermilink-optimize/runs/goal_preflight_00/measured_1.stderr.log"
            in repair_prompts[0]
        )

    def test_run_goal_campaign_applies_cli_timeout_to_generated_benchmark(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo_root = tmp_path / "repo"
        repo_root.mkdir(parents=True, exist_ok=True)
        goal_path = repo_root / "goal.md"
        goal_path.write_text(
            (
                "# Optimization Goal\n\n"
                "## Package\n"
                "mypackage\n\n"
                "## Target\n"
                "Improve solver performance.\n\n"
            ),
            encoding="utf-8",
        )
        (repo_root / "solver.py").write_text("MODE = 'BASELINE'\n", encoding="utf-8")

        benchmark_yaml = (
            "schema_version: 1\n"
            "benchmark_id: goal-timeout\n"
            "repo:\n"
            "  editable_paths:\n"
            "    - solver.py\n"
            "controller:\n"
            "  timeout_seconds: 1800\n"
            "  objective:\n"
            "    primary_metric: weighted_median_wall_seconds\n"
            "    direction: minimize\n"
            "correctness:\n"
            "  mode: runner_only\n"
            "runtime:\n"
            "  mode: direct\n"
            "  command:\n"
            "    - python\n"
            "    - .fermilink-optimize/autogen/benchmark_runner.py\n"
            "    - --benchmark\n"
            '    - "{benchmark}"\n'
            "    - --emit-json\n"
            "cases:\n"
            "  - id: train-a\n"
            "    weight: 1.0\n"
        )
        runner_script = "import argparse\n"
        analysis_payload = {
            "package": "mypackage",
            "language": "python",
            "entry_points": [],
            "editable_paths": ["solver.py"],
            "immutable_paths": [".fermilink-optimize/**", "skills/**"],
        }

        preflight_capture: dict[str, int] = {}

        monkeypatch.chdir(repo_root)
        monkeypatch.setattr(cli, "_ensure_compile_repo_ready", lambda _repo: False)
        monkeypatch.setattr(
            cli,
            "resolve_agent_runtime_policy",
            lambda: argparse.Namespace(
                provider="codex",
                sandbox_policy="enforce",
                sandbox_mode="workspace-write",
                model=None,
                reasoning_effort=None,
            ),
        )
        monkeypatch.setattr(
            optimize_git,
            "temporary_optimize_agents",
            lambda *args, **kwargs: contextlib.nullcontext(),
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_analysis_turn",
            lambda **_kwargs: {
                "assistant_text": (
                    f"<source_analysis>\n{json.dumps(analysis_payload)}\n</source_analysis>\n"
                    "<analysis_summary>ok</analysis_summary>\n"
                )
            },
        )
        monkeypatch.setattr(
            optimize_controller,
            "_collect_tracked_files",
            lambda _project_root: ["goal.md", "solver.py"],
        )
        monkeypatch.setattr(
            optimize_controller,
            "_run_goal_generation_turn",
            lambda **_kwargs: {
                "assistant_text": (
                    f"<benchmark_yaml>\n{benchmark_yaml}\n</benchmark_yaml>\n"
                    f"<runner_script>\n{runner_script}\n</runner_script>\n"
                )
            },
        )

        def fake_run_benchmark_suite(
            project_root: Path,
            *,
            benchmark_path: Path,
            benchmark_payload: dict[str, object],
            run_dir: Path,
            timeout_seconds: int,
            runtime_override: dict[str, object] | None = None,
        ) -> dict[str, object]:
            del project_root, benchmark_path, run_dir, runtime_override
            preflight_capture["timeout_seconds"] = timeout_seconds
            controller = (
                benchmark_payload.get("controller")
                if isinstance(benchmark_payload, dict)
                else {}
            )
            if isinstance(controller, dict):
                preflight_capture["controller_timeout_seconds"] = int(
                    controller.get("timeout_seconds") or 0
                )
            return {
                "status": "ok",
                "correctness_ok": True,
                "summary_metrics": {
                    "weighted_median_wall_seconds": 1.0,
                },
                "cases": [
                    {
                        "id": "train-a",
                        "converged": True,
                        "error": "",
                    }
                ],
            }

        monkeypatch.setattr(
            optimize_controller,
            "_run_benchmark_suite",
            fake_run_benchmark_suite,
        )
        monkeypatch.setattr(
            optimize_controller,
            "run_campaign",
            lambda _campaign_args: {"status": "completed"},
        )

        args = argparse.Namespace(
            package_id="goal.md",
            hpc_profile=None,
            sandbox=None,
            skills_source="existing",
            resume=False,
            timeout_seconds=7200,
        )
        payload = optimize_controller.run_goal_campaign(args)

        assert payload["goal_mode"] is True
        assert preflight_capture["timeout_seconds"] == 7200
        assert preflight_capture["controller_timeout_seconds"] == 7200
        assert (
            "timeout_seconds: 7200"
            in optimize_state.goal_benchmark_path(repo_root).read_text(encoding="utf-8")
        )


# ---------------------------------------------------------------------------
# Validation helpers (unit tests)
# ---------------------------------------------------------------------------


class TestValidationHelpers:
    def test_validate_goal_runner_valid_python(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        runner = tmp_path / "runner.py"
        runner.write_text("import sys\nprint('ok')\n", encoding="utf-8")
        error = _validate_goal_runner(runner, language="python")
        assert error == ""

    def test_validate_goal_runner_syntax_error(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        runner = tmp_path / "runner.py"
        runner.write_text("def broken(\n", encoding="utf-8")
        error = _validate_goal_runner(runner, language="python")
        assert "syntax error" in error.lower()

    def test_validate_goal_runner_missing(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        runner = tmp_path / "nonexistent.py"
        error = _validate_goal_runner(runner, language="python")
        assert "not generated" in error.lower()

    def test_validate_goal_runner_non_python(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        runner = tmp_path / "runner.sh"
        runner.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        # Non-python runners skip syntax check
        error = _validate_goal_runner(runner, language="fortran")
        assert error == ""

    def test_validate_goal_runner_contract_requires_emit_json(
        self, tmp_path: Path
    ) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        benchmark = tmp_path / "benchmark.yaml"
        benchmark.write_text(
            (
                "schema_version: 1\n"
                "benchmark_id: contract-test\n"
                "repo:\n"
                "  editable_paths:\n"
                "    - solver.py\n"
                "controller:\n"
                "  objective:\n"
                "    primary_metric: weighted_median_wall_seconds\n"
                "    direction: minimize\n"
                "runtime:\n"
                "  mode: direct\n"
                "  command:\n"
                "    - python\n"
                "    - benchmark_runner.py\n"
                "    - --benchmark\n"
                '    - "{benchmark}"\n'
            ),
            encoding="utf-8",
        )
        benchmark_payload, error = optimize_controller._validate_goal_benchmark(
            benchmark
        )
        assert error == ""
        assert benchmark_payload is not None
        runner = tmp_path / "benchmark_runner.py"
        runner.write_text("import argparse\n", encoding="utf-8")
        runner_error = _validate_goal_runner(
            runner,
            language="python",
            project_root=tmp_path,
            benchmark_path=benchmark,
            benchmark_payload=benchmark_payload,
        )
        assert "emit-json" in runner_error.lower()

    def test_validate_goal_runner_contract_happy_path(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_runner

        benchmark = tmp_path / "benchmark.yaml"
        benchmark.write_text(
            (
                "schema_version: 1\n"
                "benchmark_id: contract-test\n"
                "repo:\n"
                "  editable_paths:\n"
                "    - solver.py\n"
                "controller:\n"
                "  objective:\n"
                "    primary_metric: weighted_median_wall_seconds\n"
                "    direction: minimize\n"
                "runtime:\n"
                "  mode: direct\n"
                "  command:\n"
                "    - python\n"
                "    - benchmark_runner.py\n"
                "    - --benchmark\n"
                '    - "{benchmark}"\n'
                "    - --emit-json\n"
            ),
            encoding="utf-8",
        )
        benchmark_payload, error = optimize_controller._validate_goal_benchmark(
            benchmark
        )
        assert error == ""
        assert benchmark_payload is not None
        runner = tmp_path / "benchmark_runner.py"
        runner.write_text("import argparse\n", encoding="utf-8")
        runner_error = _validate_goal_runner(
            runner,
            language="python",
            project_root=tmp_path,
            benchmark_path=benchmark,
            benchmark_payload=benchmark_payload,
        )
        assert runner_error == ""

    def test_validate_goal_benchmark_missing(self, tmp_path: Path) -> None:
        from fermilink.optimize.main import _validate_goal_benchmark

        path = tmp_path / "nonexistent.yaml"
        payload, error = _validate_goal_benchmark(path)
        assert payload is None
        assert "not generated" in error.lower()
