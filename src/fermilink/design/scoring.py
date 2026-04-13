"""Deterministic candidate ranking heuristics."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .ir import AlgorithmSketch


@dataclass(frozen=True)
class SearchProfile:
    name: str
    gain_weight: float
    regime_fit_weight: float
    feasibility_weight: float
    novelty_weight: float
    confidence_weight: float
    structural_distance_weight: float
    risk_penalty_weight: float
    same_family_penalty: float
    same_mechanism_penalty: float
    same_complexity_penalty: float
    known_penalty: float
    close_variant_penalty: float
    plausible_novelty_floor: float


SEARCH_PROFILES: dict[str, SearchProfile] = {
    "conservative": SearchProfile(
        name="conservative",
        gain_weight=0.30,
        regime_fit_weight=0.22,
        feasibility_weight=0.25,
        novelty_weight=0.08,
        confidence_weight=0.15,
        structural_distance_weight=0.00,
        risk_penalty_weight=0.36,
        same_family_penalty=0.03,
        same_mechanism_penalty=0.10,
        same_complexity_penalty=0.08,
        known_penalty=0.25,
        close_variant_penalty=0.12,
        plausible_novelty_floor=0.70,
    ),
    "balanced": SearchProfile(
        name="balanced",
        gain_weight=0.33,
        regime_fit_weight=0.24,
        feasibility_weight=0.18,
        novelty_weight=0.15,
        confidence_weight=0.10,
        structural_distance_weight=0.00,
        risk_penalty_weight=0.28,
        same_family_penalty=0.05,
        same_mechanism_penalty=0.15,
        same_complexity_penalty=0.10,
        known_penalty=0.20,
        close_variant_penalty=0.10,
        plausible_novelty_floor=0.70,
    ),
    "novelty-seeking": SearchProfile(
        name="novelty-seeking",
        gain_weight=0.30,
        regime_fit_weight=0.24,
        feasibility_weight=0.12,
        novelty_weight=0.22,
        confidence_weight=0.08,
        structural_distance_weight=0.12,
        risk_penalty_weight=0.20,
        same_family_penalty=0.10,
        same_mechanism_penalty=0.20,
        same_complexity_penalty=0.12,
        known_penalty=0.18,
        close_variant_penalty=0.14,
        plausible_novelty_floor=0.75,
    ),
    "moonshot": SearchProfile(
        name="moonshot",
        gain_weight=0.28,
        regime_fit_weight=0.24,
        feasibility_weight=0.06,
        novelty_weight=0.26,
        confidence_weight=0.06,
        structural_distance_weight=0.18,
        risk_penalty_weight=0.14,
        same_family_penalty=0.14,
        same_mechanism_penalty=0.25,
        same_complexity_penalty=0.14,
        known_penalty=0.16,
        close_variant_penalty=0.16,
        plausible_novelty_floor=0.80,
    ),
}


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(token) > 2
    }


def _overlap_score(left: str, right: str) -> float:
    left_tokens = _tokenize(left)
    right_tokens = _tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    shared = len(left_tokens & right_tokens)
    return shared / max(len(left_tokens), len(right_tokens))


def _structural_distance(candidate: AlgorithmSketch, baseline: AlgorithmSketch) -> float:
    checks = [
        candidate.family.casefold() != baseline.family.casefold(),
        candidate.mechanism.casefold() != baseline.mechanism.casefold(),
        candidate.time_complexity.casefold() != baseline.time_complexity.casefold(),
    ]
    scored = [1.0 for check in checks if check]
    if not scored:
        return 0.0
    return len(scored) / len(checks)


def get_search_profile(name: str | None) -> SearchProfile:
    key = str(name or "balanced").strip().casefold()
    if key not in SEARCH_PROFILES:
        return SEARCH_PROFILES["balanced"]
    return SEARCH_PROFILES[key]


def score_candidate(
    candidate: AlgorithmSketch,
    *,
    baseline: AlgorithmSketch,
    goal_spec: dict[str, Any],
    novelty_label: str,
    search_profile: str = "balanced",
) -> dict[str, float]:
    profile = get_search_profile(search_profile)
    regime_fit = max(
        candidate.regime_fit_score,
        _overlap_score(candidate.target_regime, str(goal_spec.get("target_regime") or "")),
    )
    gain = candidate.predicted_gain_score
    feasibility = candidate.feasibility_score
    novelty = candidate.novelty_score
    confidence = candidate.confidence
    risk_penalty = candidate.risk_score
    structural_distance = _structural_distance(candidate, baseline)

    if candidate.family and candidate.family.casefold() == baseline.family.casefold():
        risk_penalty += profile.same_family_penalty
    if candidate.mechanism and candidate.mechanism.casefold() == baseline.mechanism.casefold():
        risk_penalty += profile.same_mechanism_penalty
    if (
        candidate.time_complexity
        and baseline.time_complexity
        and candidate.time_complexity.casefold() == baseline.time_complexity.casefold()
    ):
        risk_penalty += profile.same_complexity_penalty

    if novelty_label == "known":
        risk_penalty += profile.known_penalty
    elif novelty_label == "close_variant":
        risk_penalty += profile.close_variant_penalty
    elif novelty_label == "plausible_novel_composition":
        novelty = max(novelty, profile.plausible_novelty_floor)

    total = (
        profile.gain_weight * gain
        + profile.regime_fit_weight * regime_fit
        + profile.feasibility_weight * feasibility
        + profile.novelty_weight * novelty
        + profile.confidence_weight * confidence
        + profile.structural_distance_weight * structural_distance
        - profile.risk_penalty_weight * min(risk_penalty, 1.0)
    )
    return {
        "search_profile": profile.name,
        "gain": round(gain, 4),
        "regime_fit": round(regime_fit, 4),
        "feasibility": round(feasibility, 4),
        "novelty": round(novelty, 4),
        "confidence": round(confidence, 4),
        "structural_distance": round(structural_distance, 4),
        "risk_penalty": round(min(risk_penalty, 1.0), 4),
        "total": round(total, 4),
    }
