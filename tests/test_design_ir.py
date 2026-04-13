from __future__ import annotations

from fermilink.design.ir import AlgorithmSketch


def test_algorithm_sketch_from_payload_normalizes_and_builds_signature() -> None:
    sketch = AlgorithmSketch.from_payload(
        {
            "title": "Hierarchical screened solver",
            "family": "hierarchical decomposition",
            "mechanism": "screened block tree",
            "target_regime": "large N sparse systems",
            "dominant_kernel": "long-range interaction aggregation",
            "main_steps": ["build tree", "screen blocks", "accumulate"],
            "required_invariants": ["preserve symmetry", "preserve symmetry"],
            "predicted_gain_score": 1.2,
            "risk_score": -2.0,
        },
        role="candidate",
    )

    assert sketch.hypothesis_id
    assert sketch.required_invariants == ["preserve symmetry"]
    assert sketch.predicted_gain_score == 1.0
    assert sketch.risk_score == 0.0
    assert "hierarchical-decomposition" in sketch.canonical_signature()
