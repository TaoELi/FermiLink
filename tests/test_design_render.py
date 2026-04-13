from __future__ import annotations

from fermilink.design.ir import AlgorithmSketch
from fermilink.design.render import (
    render_baseline_report,
    render_candidate_pseudocode,
    render_shortlist_report,
)


def _baseline() -> AlgorithmSketch:
    return AlgorithmSketch.from_payload(
        {
            "title": "Baseline",
            "family": "iterative solver",
            "mechanism": "Davidson",
            "target_regime": "moderate systems",
            "dominant_kernel": "matvec",
            "main_steps": ["build subspace", "solve projected problem"],
            "bottlenecks": ["orthogonalization"],
        },
        role="baseline",
        hypothesis_id="baseline",
    )


def test_render_baseline_report_contains_core_sections() -> None:
    text = render_baseline_report(
        _baseline(),
        "Baseline summary.",
        extractor_summary="Extractor summary.",
        audit_payload={
            "field_verdicts": [
                {
                    "field": "family",
                    "status": "confirmed_by_code",
                    "evidence": "Matches the implementation path.",
                    "source_paths": ["solver.py"],
                }
            ],
            "disagreements": [
                {
                    "field": "mechanism",
                    "extractor_claim": "Lanczos-like restart",
                    "audit_finding": "Davidson-style projected solve",
                    "basis": "Residual correction vectors are explicitly formed.",
                }
            ],
            "uncertainties": ["Exact crossover regime remains uncertain."],
        },
        audit_summary="Audit summary.",
        publication_payload={
            "internet_used": True,
            "references": [
                {
                    "title": "Canonical Davidson paper",
                    "url": "https://example.com/davidson",
                    "type": "paper",
                }
            ],
            "publication_conflicts": [
                {
                    "field": "mechanism",
                    "publication_claim": "Original formulation",
                    "code_finding": "Current code includes block heuristics",
                    "resolution": "prefer_code",
                }
            ],
            "canonical_terms": ["Davidson", "subspace iteration"],
            "evidence_gaps": ["Need implementation-age-specific citations."],
        },
        publication_summary="Publication summary.",
    )
    assert "# Baseline Sketch" in text
    assert "## Extractor Summary" in text
    assert "## Audit Summary" in text
    assert "## Field Verdicts" in text
    assert "## Publication Check" in text
    assert "## Main Steps" in text
    assert "## Bottlenecks" in text


def test_render_candidate_pseudocode_and_shortlist() -> None:
    baseline = _baseline()
    candidate = AlgorithmSketch.from_payload(
        {
            "title": "Low-rank candidate",
            "family": "low-rank",
            "mechanism": "factorized subspace update",
            "target_regime": "large systems",
            "dominant_kernel": "compressed update",
            "pseudocode_outline": ["compress basis", "solve reduced system"],
            "expected_advantages": ["lower memory"],
        },
        role="candidate",
        parent_id="baseline",
    )
    pseudo = render_candidate_pseudocode(
        candidate,
        score=0.71,
        novelty={"label": "plausible_novel_composition", "summary": "Needs review."},
        baseline=baseline,
    )
    assert "## Pseudocode" in pseudo
    assert "compress basis" in pseudo

    shortlist = render_shortlist_report(
        goal_spec={
            "package": "pkg",
            "scientific_problem": "solver design",
            "target_kernel": "eigensolver",
            "target_regime": "large systems",
        },
        baseline=baseline,
        archive_rows=[
            {
                "rank": 1,
                "score": 0.71,
                "novelty": {"label": "plausible_novel_composition"},
                "sketch": candidate.to_dict(),
            }
        ],
        shortlist_size=3,
        baseline_summary="Baseline summary.",
        search_summary="Search summary.",
        search_profile="balanced",
    )
    assert "| Rank | Hypothesis |" in shortlist
    assert "Low-rank candidate" in shortlist
    assert "Search profile: `balanced`" in shortlist
