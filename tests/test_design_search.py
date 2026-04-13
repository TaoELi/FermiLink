from __future__ import annotations

from fermilink.design import prompts
from fermilink.design.ir import AlgorithmSketch
from fermilink.design.search import build_ranked_archive


def _baseline() -> AlgorithmSketch:
    return AlgorithmSketch.from_payload(
        {
            "title": "Davidson baseline",
            "problem": "eigensolver",
            "family": "iterative solver",
            "mechanism": "expanding Davidson subspace",
            "target_regime": "moderate systems",
            "dominant_kernel": "matvec and orthogonalization",
            "time_complexity": "O(k * matvec + k^2 N)",
            "memory_complexity": "O(kN)",
        },
        role="baseline",
        hypothesis_id="baseline",
    )


def test_build_ranked_archive_deduplicates_and_scores() -> None:
    baseline = _baseline()
    candidates = [
        AlgorithmSketch.from_payload(
            {
                "title": "Screened block solver",
                "family": "hierarchical decomposition",
                "mechanism": "screened block tree",
                "target_regime": "large N sparse systems",
                "dominant_kernel": "screened block aggregation",
                "time_complexity": "O(N log N)",
                "memory_complexity": "O(N)",
                "predicted_gain_score": 0.9,
                "regime_fit_score": 0.9,
                "feasibility_score": 0.6,
                "novelty_score": 0.8,
                "confidence": 0.7,
                "risk_score": 0.2,
                "novelty_signature": ["screened", "block tree"],
            },
            role="candidate",
            parent_id="baseline",
        ),
        AlgorithmSketch.from_payload(
            {
                "title": "Screened block solver copy",
                "family": "hierarchical decomposition",
                "mechanism": "screened block tree",
                "target_regime": "large N sparse systems",
                "dominant_kernel": "screened block aggregation",
                "time_complexity": "O(N log N)",
                "memory_complexity": "O(N)",
                "predicted_gain_score": 0.8,
                "confidence": 0.6,
            },
            role="candidate",
            parent_id="baseline",
        ),
        AlgorithmSketch.from_payload(
            {
                "title": "Davidson threshold retune",
                "family": "iterative solver",
                "mechanism": "expanding Davidson subspace",
                "target_regime": "moderate systems",
                "dominant_kernel": "matvec and orthogonalization",
                "time_complexity": "O(k * matvec + k^2 N)",
                "memory_complexity": "O(kN)",
                "predicted_gain_score": 0.2,
                "feasibility_score": 0.9,
                "novelty_score": 0.1,
                "confidence": 0.8,
            },
            role="candidate",
            parent_id="baseline",
        ),
    ]

    archive = build_ranked_archive(
        candidates,
        baseline=baseline,
        goal_spec={"target_regime": "large N sparse systems"},
        local_corpus="The local docs discuss Davidson and screened methods.",
    )

    assert len(archive) == 2
    assert archive[0]["sketch"]["title"] == "Screened block solver"
    assert archive[0]["novelty"]["label"] in {
        "close_variant",
        "plausible_novel_composition",
    }
    assert archive[0]["rank"] == 1


def test_build_ranked_archive_search_profiles_shift_priorities() -> None:
    baseline = _baseline()
    safe_candidate = AlgorithmSketch.from_payload(
        {
            "title": "Davidson threshold retune",
            "family": "iterative solver",
            "mechanism": "expanding Davidson subspace",
            "target_regime": "moderate systems",
            "dominant_kernel": "matvec and orthogonalization",
            "time_complexity": "O(k * matvec + k^2 N)",
            "memory_complexity": "O(kN)",
            "predicted_gain_score": 0.45,
            "regime_fit_score": 0.95,
            "feasibility_score": 0.95,
            "novelty_score": 0.15,
            "confidence": 0.9,
            "risk_score": 0.1,
            "novelty_signature": ["threshold", "damping"],
        },
        role="candidate",
        parent_id="baseline",
    )
    bold_candidate = AlgorithmSketch.from_payload(
        {
            "title": "Screened block tree solver",
            "family": "hierarchical decomposition",
            "mechanism": "screened block tree",
            "target_regime": "large N sparse systems",
            "dominant_kernel": "screened block aggregation",
            "time_complexity": "O(N log N)",
            "memory_complexity": "O(N)",
            "predicted_gain_score": 0.75,
            "regime_fit_score": 0.4,
            "feasibility_score": 0.35,
            "novelty_score": 0.85,
            "confidence": 0.55,
            "risk_score": 0.4,
            "novelty_signature": ["screened", "block tree"],
        },
        role="candidate",
        parent_id="baseline",
    )

    conservative = build_ranked_archive(
        [safe_candidate, bold_candidate],
        baseline=baseline,
        goal_spec={"target_regime": "moderate systems"},
        local_corpus="The local docs discuss Davidson and threshold damping.",
        search_profile="conservative",
    )
    novelty_seeking = build_ranked_archive(
        [safe_candidate, bold_candidate],
        baseline=baseline,
        goal_spec={"target_regime": "moderate systems"},
        local_corpus="The local docs discuss Davidson and threshold damping.",
        search_profile="novelty-seeking",
    )

    assert conservative[0]["sketch"]["title"] == "Davidson threshold retune"
    assert conservative[0]["search_profile"] == "conservative"
    assert novelty_seeking[0]["sketch"]["title"] == "Screened block tree solver"
    assert novelty_seeking[0]["search_profile"] == "novelty-seeking"


def test_candidate_search_prompt_includes_profile_guidance() -> None:
    prompt = prompts.build_candidate_search_prompt(
        goal_spec={"raw_text": "## Scientific Problem\nFind a better solver.\n"},
        baseline_sketch=_baseline().to_dict(),
        repo_summary="solver.py",
        evidence_summary="README.md discusses Davidson.",
        family_catalog="- hierarchical decomposition",
        max_candidates=8,
        search_profile="moonshot",
    )

    assert "Selected profile: `moonshot`" in prompt
    assert "Allocate most of the candidate budget to structurally different families" in prompt
    assert "Search profile guidance" in prompt
