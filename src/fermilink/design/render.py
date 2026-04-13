"""Human-readable rendering for design-mode artifacts."""

from __future__ import annotations

from typing import Any

from .ir import AlgorithmSketch


def _render_list(title: str, values: list[str]) -> str:
    if not values:
        return f"## {title}\n- (none)\n"
    lines = [f"## {title}"]
    lines.extend(f"- {item}" for item in values)
    return "\n".join(lines) + "\n"


def render_baseline_report(sketch: AlgorithmSketch, summary: str) -> str:
    parts = [
        f"# Baseline Sketch: {sketch.title}",
        "",
        f"Family: `{sketch.family}`",
        f"Mechanism: {sketch.mechanism}",
        f"Target regime: {sketch.target_regime}",
        f"Dominant kernel: {sketch.dominant_kernel}",
        "",
    ]
    if summary:
        parts.extend(["## Summary", summary, ""])
    parts.append(_render_list("Main Steps", sketch.rendered_steps()).rstrip())
    parts.append("")
    parts.append(_render_list("Bottlenecks", sketch.bottlenecks).rstrip())
    parts.append("")
    parts.extend(
        [
            "## Complexity",
            f"- Time: {sketch.time_complexity or '(unspecified)'}",
            f"- Memory: {sketch.memory_complexity or '(unspecified)'}",
            f"- Communication: {sketch.communication_complexity or '(unspecified)'}",
            "",
        ]
    )
    parts.append(_render_list("Required Invariants", sketch.required_invariants).rstrip())
    parts.append("")
    return "\n".join(parts).strip() + "\n"


def render_candidate_pseudocode(
    sketch: AlgorithmSketch,
    *,
    score: float,
    novelty: dict[str, Any],
    baseline: AlgorithmSketch,
) -> str:
    lines = [
        f"# Candidate Hypothesis: {sketch.title}",
        "",
        f"Rank score: `{score:.4f}`",
        f"Family: `{sketch.family}`",
        f"Mechanism: {sketch.mechanism}",
        f"Target regime: {sketch.target_regime}",
        f"Dominant kernel: {sketch.dominant_kernel}",
        f"Novelty risk: `{novelty.get('label', 'unknown')}`",
        "",
        "## Pseudocode",
    ]
    steps = sketch.rendered_steps() or ["(no pseudocode outline provided)"]
    for index, step in enumerate(steps, start=1):
        lines.append(f"{index}. {step}")
    lines.extend(
        [
            "",
            "## Complexity",
            f"- Time: {sketch.time_complexity or '(unspecified)'}",
            f"- Memory: {sketch.memory_complexity or '(unspecified)'}",
            f"- Communication: {sketch.communication_complexity or '(unspecified)'}",
            "",
            "## Claimed Advantages",
        ]
    )
    if sketch.expected_advantages:
        lines.extend(f"- {item}" for item in sketch.expected_advantages)
    else:
        lines.append("- (none stated)")
    lines.extend(
        [
            "",
            "## Tradeoffs",
        ]
    )
    if sketch.expected_disadvantages:
        lines.extend(f"- {item}" for item in sketch.expected_disadvantages)
    else:
        lines.append("- (none stated)")
    lines.extend(
        [
            "",
            "## Comparison To Baseline",
            f"- Baseline family: `{baseline.family}`",
            f"- Baseline mechanism: {baseline.mechanism}",
            f"- Candidate rationale: {sketch.scientific_rationale or '(not provided)'}",
            "",
            "## Novelty Assessment",
            f"- Summary: {novelty.get('summary', '(none)')}",
        ]
    )
    matches = novelty.get("local_matches")
    if isinstance(matches, list) and matches:
        lines.extend(f"- Local evidence match: `{item}`" for item in matches)
    gaps = novelty.get("evidence_gaps")
    if isinstance(gaps, list) and gaps:
        lines.extend(f"- Evidence gap: {item}" for item in gaps)
    return "\n".join(lines).strip() + "\n"


def render_candidate_comparison(
    sketch: AlgorithmSketch,
    *,
    score_breakdown: dict[str, Any],
    novelty: dict[str, Any],
    baseline: AlgorithmSketch,
) -> str:
    return (
        f"# Comparison: {baseline.title} vs {sketch.title}\n\n"
        f"| Field | Baseline | Candidate |\n"
        f"| --- | --- | --- |\n"
        f"| Family | {baseline.family or '-'} | {sketch.family or '-'} |\n"
        f"| Mechanism | {baseline.mechanism or '-'} | {sketch.mechanism or '-'} |\n"
        f"| Target regime | {baseline.target_regime or '-'} | {sketch.target_regime or '-'} |\n"
        f"| Time complexity | {baseline.time_complexity or '-'} | {sketch.time_complexity or '-'} |\n"
        f"| Memory complexity | {baseline.memory_complexity or '-'} | {sketch.memory_complexity or '-'} |\n"
        f"| Novelty risk | - | {novelty.get('label', '-')} |\n\n"
        "## Score breakdown\n"
        f"- Total: {score_breakdown.get('total')}\n"
        f"- Gain: {score_breakdown.get('gain')}\n"
        f"- Regime fit: {score_breakdown.get('regime_fit')}\n"
        f"- Feasibility: {score_breakdown.get('feasibility')}\n"
        f"- Novelty: {score_breakdown.get('novelty')}\n"
        f"- Confidence: {score_breakdown.get('confidence')}\n"
        f"- Risk penalty: {score_breakdown.get('risk_penalty')}\n"
    )


def render_novelty_report(novelty: dict[str, Any]) -> str:
    lines = [
        "# Novelty Dossier",
        "",
        f"Label: `{novelty.get('label', 'unknown')}`",
        f"Summary: {novelty.get('summary', '(none)')}",
        "",
        "## Matched Terms",
    ]
    matched_terms = novelty.get("matched_terms")
    if isinstance(matched_terms, list) and matched_terms:
        lines.extend(f"- `{item}`" for item in matched_terms)
    else:
        lines.append("- (none)")
    lines.extend(["", "## Evidence Gaps"])
    gaps = novelty.get("evidence_gaps")
    if isinstance(gaps, list) and gaps:
        lines.extend(f"- {item}" for item in gaps)
    else:
        lines.append("- (none)")
    return "\n".join(lines).strip() + "\n"


def render_shortlist_report(
    *,
    goal_spec: dict[str, Any],
    baseline: AlgorithmSketch,
    archive_rows: list[dict[str, Any]],
    shortlist_size: int,
    baseline_summary: str,
    search_summary: str,
    search_profile: str,
) -> str:
    top_rows = archive_rows[:shortlist_size]
    lines = [
        "# FermiLink Design Shortlist",
        "",
        f"Package: `{goal_spec.get('package', '')}`",
        f"Scientific problem: {goal_spec.get('scientific_problem', '')}",
        f"Target kernel: {goal_spec.get('target_kernel', '')}",
        f"Target regime: {goal_spec.get('target_regime', '')}",
        f"Search profile: `{search_profile}`",
        "",
        "## Baseline",
        f"- Title: {baseline.title}",
        f"- Family: `{baseline.family}`",
        f"- Mechanism: {baseline.mechanism}",
        f"- Dominant kernel: {baseline.dominant_kernel}",
    ]
    if baseline_summary:
        lines.append(f"- Summary: {baseline_summary}")
    if search_summary:
        lines.extend(["", "## Search Summary", search_summary])
    lines.extend(
        [
            "",
            "## Shortlist",
            "| Rank | Hypothesis | Family | Regime | Score | Novelty |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    for row in top_rows:
        sketch = row.get("sketch") or {}
        novelty = row.get("novelty") or {}
        lines.append(
            "| {rank} | {title} | {family} | {regime} | {score:.4f} | {novelty_label} |".format(
                rank=row.get("rank"),
                title=str(sketch.get("title") or "").replace("|", "/"),
                family=str(sketch.get("family") or "-").replace("|", "/"),
                regime=str(sketch.get("target_regime") or "-").replace("|", "/"),
                score=float(row.get("score") or 0.0),
                novelty_label=str(novelty.get("label") or "-").replace("|", "/"),
            )
        )
    return "\n".join(lines).strip() + "\n"
