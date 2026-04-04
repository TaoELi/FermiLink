"""Tests for goal-driven optimize mode (goal.md parsing, detection, prompts)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli import optimize_goal
from fermilink.cli import optimize_source_analysis
from fermilink.cli import optimize_state
from fermilink.cli.commands.optimize import _resolve_optimize_mode


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
        text = "<analysis_summary>Found 3 entry points and 5 test cases</analysis_summary>"
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
        )
        assert "pyscf" in prompt
        assert "benchmark_yaml" in prompt
        assert "runner_script" in prompt
        assert "runner_only" in prompt or "field_tolerances" in prompt


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

    def test_plain_prompt_stays_quick(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text(NOT_A_GOAL, encoding="utf-8")
        args = argparse.Namespace(
            package_id=str(prompt_file),
            project_path=None,
            benchmark=None,
            goal=False,
        )
        assert _resolve_optimize_mode(args) == "quick"

    def test_parser_accepts_goal_flag(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["optimize", "goal.md", "--goal"])
        assert args.goal is True


# ---------------------------------------------------------------------------
# Validation helpers (unit tests)
# ---------------------------------------------------------------------------


class TestValidationHelpers:
    def test_validate_goal_runner_valid_python(self, tmp_path: Path) -> None:
        from fermilink.cli.optimize_controller import _validate_goal_runner

        runner = tmp_path / "runner.py"
        runner.write_text("import sys\nprint('ok')\n", encoding="utf-8")
        error = _validate_goal_runner(runner, language="python")
        assert error == ""

    def test_validate_goal_runner_syntax_error(self, tmp_path: Path) -> None:
        from fermilink.cli.optimize_controller import _validate_goal_runner

        runner = tmp_path / "runner.py"
        runner.write_text("def broken(\n", encoding="utf-8")
        error = _validate_goal_runner(runner, language="python")
        assert "syntax error" in error.lower()

    def test_validate_goal_runner_missing(self, tmp_path: Path) -> None:
        from fermilink.cli.optimize_controller import _validate_goal_runner

        runner = tmp_path / "nonexistent.py"
        error = _validate_goal_runner(runner, language="python")
        assert "not generated" in error.lower()

    def test_validate_goal_runner_non_python(self, tmp_path: Path) -> None:
        from fermilink.cli.optimize_controller import _validate_goal_runner

        runner = tmp_path / "runner.sh"
        runner.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        # Non-python runners skip syntax check
        error = _validate_goal_runner(runner, language="fortran")
        assert error == ""

    def test_validate_goal_benchmark_missing(self, tmp_path: Path) -> None:
        from fermilink.cli.optimize_controller import _validate_goal_benchmark

        path = tmp_path / "nonexistent.yaml"
        payload, error = _validate_goal_benchmark(path)
        assert payload is None
        assert "not generated" in error.lower()
