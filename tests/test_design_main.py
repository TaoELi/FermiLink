from __future__ import annotations

import importlib
from pathlib import Path

from fermilink.design.ir import AlgorithmSketch
from fermilink.design.runtime import DesignTurnResult


design_main = importlib.import_module("fermilink.design.main")


SAMPLE_GOAL = """\
# Design Goal

## Package
demo-pkg

## Scientific Problem
Find a more scalable algorithm for a tensor-heavy solver.

## Target Kernel
Projected eigensolver update.

## Target Regime
Large systems with many requested roots.

## Current Baseline
Dense Davidson-style subspace iterations.

## Required Invariants
- Preserve energies within tolerance
"""


def test_design_main_run_pipeline_writes_phase1_artifacts(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "README.md").write_text("Local docs about Davidson.", encoding="utf-8")
    (project_root / "solver.py").write_text("def kernel():\n    return 1\n", encoding="utf-8")
    goal_path = project_root / "goal.md"
    goal_path.write_text(SAMPLE_GOAL, encoding="utf-8")

    baseline_sketch = AlgorithmSketch.from_payload(
        {
            "title": "Davidson baseline",
            "family": "iterative solver",
            "mechanism": "expanding Davidson subspace",
            "target_regime": "moderate systems",
            "dominant_kernel": "matvec and orthogonalization",
            "main_steps": ["build subspace", "orthogonalize", "solve reduced problem"],
            "bottlenecks": ["orthogonalization"],
        },
        role="baseline",
        hypothesis_id="baseline",
    )
    candidate = AlgorithmSketch.from_payload(
        {
            "title": "Low-rank block candidate",
            "family": "low-rank factorization",
            "mechanism": "factorized block update",
            "target_regime": "large systems with many roots",
            "dominant_kernel": "compressed block solve",
            "pseudocode_outline": ["compress subspace blocks", "solve reduced block problem"],
            "expected_advantages": ["lower memory", "better scaling at large root count"],
            "predicted_gain_score": 0.85,
            "regime_fit_score": 0.90,
            "feasibility_score": 0.60,
            "novelty_score": 0.72,
            "confidence": 0.66,
            "risk_score": 0.25,
            "novelty_signature": ["low-rank", "block update"],
        },
        role="candidate",
        parent_id="baseline",
    )
    captured: dict[str, object] = {}

    def fake_run_baseline_analysis(**_kwargs):
        return baseline_sketch, "Baseline summary.", DesignTurnResult("", 0, "", False)

    def fake_run_candidate_search(**_kwargs):
        captured["search_profile"] = _kwargs.get("search_profile")
        return [candidate], "Search summary.", DesignTurnResult("", 0, "", False)

    monkeypatch.setattr(design_main.baseline, "run_baseline_analysis", fake_run_baseline_analysis)
    monkeypatch.setattr(design_main.search, "run_candidate_search", fake_run_candidate_search)

    payload = design_main.run_pipeline(
        design_main._build_parser().parse_args(
            [
                str(goal_path),
                "--project-root",
                str(project_root),
                "--max-candidates",
                "4",
                "--shortlist-size",
                "2",
                "--search-profile",
                "moonshot",
            ]
        )
    )

    design_root = project_root / ".fermilink-design"
    assert payload["shortlist"]
    assert payload["search_profile"] == "moonshot"
    assert captured["search_profile"] == "moonshot"
    assert (design_root / "goal.json").exists()
    assert (design_root / "baseline" / "sketch.json").exists()
    assert (design_root / "reports" / "shortlist.md").exists()
    assert (design_root / "candidates" / candidate.hypothesis_id / "pseudocode.md").exists()
    summary_text = (design_root / "reports" / "shortlist.md").read_text(encoding="utf-8")
    assert "Search profile: `moonshot`" in summary_text
    session_dirs = sorted((design_root / "sessions").glob("*"))
    assert session_dirs
    manifest_text = (session_dirs[-1] / "manifest.json").read_text(encoding="utf-8")
    assert '"search_profile": "moonshot"' in manifest_text
