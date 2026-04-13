"""Local novelty-risk assessment for candidate sketches."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .ir import AlgorithmSketch


def _tokenize_terms(values: list[str]) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        for token in re.findall(r"[a-z0-9_+\-]{3,}", value.lower()):
            if token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


@dataclass(slots=True)
class NoveltyAssessment:
    label: str
    summary: str
    matched_terms: list[str] = field(default_factory=list)
    local_matches: list[str] = field(default_factory=list)
    evidence_gaps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_candidate_novelty(
    candidate: AlgorithmSketch,
    *,
    baseline: AlgorithmSketch,
    local_corpus: str,
) -> NoveltyAssessment:
    if candidate.canonical_signature() == baseline.canonical_signature():
        return NoveltyAssessment(
            label="known",
            summary="Candidate matches the baseline mechanism/signature.",
            matched_terms=[candidate.family, candidate.mechanism],
            evidence_gaps=["Need external literature validation before any novelty claim."],
        )

    search_terms = _tokenize_terms(
        candidate.novelty_signature
        + [candidate.family, candidate.mechanism, candidate.title]
    )
    lowered_corpus = local_corpus.lower()
    local_matches = [term for term in search_terms if term and term in lowered_corpus][:8]

    if candidate.family and candidate.family.casefold() == baseline.family.casefold():
        label = "close_variant"
        summary = "Candidate stays within the baseline family and should be treated as a close variant."
    elif len(local_matches) >= 3:
        label = "close_variant"
        summary = "Candidate aligns with terms already present in local evidence and should not be presented as novel."
    elif candidate.novelty_score >= 0.65 and candidate.family.casefold() != baseline.family.casefold():
        label = "plausible_novel_composition"
        summary = "Candidate appears mechanistically distinct in local evidence, but external prior-art review is still required."
    else:
        label = "high_risk_novelty_claim"
        summary = "Local evidence is insufficient to support a novelty claim; treat the idea as speculative."

    evidence_gaps = [
        "External literature retrieval is still required.",
        "Code-level repository search outside the local project is not part of Phase 1.",
    ]
    return NoveltyAssessment(
        label=label,
        summary=summary,
        matched_terms=search_terms[:8],
        local_matches=local_matches,
        evidence_gaps=evidence_gaps,
    )
