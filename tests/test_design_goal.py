from __future__ import annotations

from fermilink.design import goal


SAMPLE_GOAL = """\
# Design Goal

## Package
pyscf

## Scientific Problem
Search for a more efficient TDDFT excited-state solver for large systems.

## Target Kernel
Davidson-style subspace eigensolver in the TD response path.

## Target Regime
Large AO basis, many requested roots, memory pressure at large subspace size.

## Current Baseline
Expanded subspace Davidson iterations with repeated matvecs and orthogonalization.

## Required Invariants
- Preserve converged excitation energies within 1e-5 Ha
- Preserve root ordering for stable cases

## Accuracy/Error Budget
- Relative excitation error below 1e-4

## Representative Workloads
- Benzene TDDFT 10 roots
- Allyl radical TDA 8 roots

## Prior-Art Hints
- Davidson
- Lanczos

## Local Evidence
- README.md
- docs/notes.md

## Language
python
"""


def test_is_design_goal_markdown_detects_structured_goal() -> None:
    assert goal.is_design_goal_markdown(SAMPLE_GOAL)


def test_parse_goal_extracts_design_specific_sections() -> None:
    spec = goal.parse_goal(SAMPLE_GOAL)
    assert spec["package"] == "pyscf"
    assert "excited-state solver" in spec["scientific_problem"]
    assert "Davidson" in spec["target_kernel"]
    assert "Large AO basis" in spec["target_regime"]
    assert len(spec["required_invariants"]) == 2
    assert len(spec["accuracy_error_budget"]) == 1
    assert len(spec["workloads"]) == 2
    assert spec["language"] == "python"
    assert spec["local_evidence_paths"] == ["README.md", "docs/notes.md"]
