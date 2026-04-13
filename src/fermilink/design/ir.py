"""Typed sketch objects for design-mode hypothesis search."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _normalize_list(value: object) -> list[str]:
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = _normalize_text(item)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)
    return normalized


def _clamp_score(value: object) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _slug(value: str) -> str:
    compact = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return compact or "hypothesis"


def _default_hypothesis_id(title: str, family: str, mechanism: str) -> str:
    seed = "|".join((title, family, mechanism)).encode("utf-8")
    digest = hashlib.sha1(seed).hexdigest()[:8]
    stem = _slug(title or family or mechanism or "hypothesis")
    return f"{stem}-{digest}"


@dataclass(slots=True)
class AlgorithmSketch:
    hypothesis_id: str
    parent_id: str
    role: str
    title: str
    problem: str
    family: str
    mechanism: str
    target_regime: str
    dominant_kernel: str
    data_representation: str = ""
    main_steps: list[str] = field(default_factory=list)
    bottlenecks: list[str] = field(default_factory=list)
    time_complexity: str = ""
    memory_complexity: str = ""
    communication_complexity: str = ""
    expected_advantages: list[str] = field(default_factory=list)
    expected_disadvantages: list[str] = field(default_factory=list)
    required_invariants: list[str] = field(default_factory=list)
    accuracy_controls: list[str] = field(default_factory=list)
    failure_modes: list[str] = field(default_factory=list)
    novelty_signature: list[str] = field(default_factory=list)
    pseudocode_outline: list[str] = field(default_factory=list)
    scientific_rationale: str = ""
    predicted_gain_score: float = 0.0
    regime_fit_score: float = 0.0
    feasibility_score: float = 0.0
    novelty_score: float = 0.0
    risk_score: float = 0.0
    confidence: float = 0.0

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        *,
        role: str,
        parent_id: str = "",
        hypothesis_id: str | None = None,
    ) -> "AlgorithmSketch":
        title = _normalize_text(
            payload.get("title")
            or payload.get("name")
            or payload.get("hypothesis")
            or payload.get("family")
            or "Untitled hypothesis"
        )
        family = _normalize_text(payload.get("family"))
        mechanism = _normalize_text(payload.get("mechanism"))
        return cls(
            hypothesis_id=hypothesis_id
            or _normalize_text(payload.get("hypothesis_id"))
            or _default_hypothesis_id(title, family, mechanism),
            parent_id=_normalize_text(payload.get("parent_id")) or parent_id,
            role=_normalize_text(payload.get("role")) or role,
            title=title,
            problem=_normalize_text(
                payload.get("problem") or payload.get("scientific_problem")
            ),
            family=family,
            mechanism=mechanism,
            target_regime=_normalize_text(payload.get("target_regime")),
            dominant_kernel=_normalize_text(payload.get("dominant_kernel")),
            data_representation=_normalize_text(payload.get("data_representation")),
            main_steps=_normalize_list(payload.get("main_steps")),
            bottlenecks=_normalize_list(payload.get("bottlenecks")),
            time_complexity=_normalize_text(payload.get("time_complexity")),
            memory_complexity=_normalize_text(payload.get("memory_complexity")),
            communication_complexity=_normalize_text(
                payload.get("communication_complexity")
            ),
            expected_advantages=_normalize_list(payload.get("expected_advantages")),
            expected_disadvantages=_normalize_list(
                payload.get("expected_disadvantages")
            ),
            required_invariants=_normalize_list(payload.get("required_invariants")),
            accuracy_controls=_normalize_list(payload.get("accuracy_controls")),
            failure_modes=_normalize_list(payload.get("failure_modes")),
            novelty_signature=_normalize_list(payload.get("novelty_signature")),
            pseudocode_outline=_normalize_list(payload.get("pseudocode_outline")),
            scientific_rationale=_normalize_text(payload.get("scientific_rationale")),
            predicted_gain_score=_clamp_score(payload.get("predicted_gain_score")),
            regime_fit_score=_clamp_score(payload.get("regime_fit_score")),
            feasibility_score=_clamp_score(payload.get("feasibility_score")),
            novelty_score=_clamp_score(payload.get("novelty_score")),
            risk_score=_clamp_score(payload.get("risk_score")),
            confidence=_clamp_score(payload.get("confidence")),
        )

    def canonical_signature(self) -> str:
        parts = [
            self.family,
            self.mechanism,
            self.target_regime,
            self.dominant_kernel,
            self.time_complexity,
            self.memory_complexity,
        ]
        return "|".join(_slug(part) for part in parts if part)

    def rendered_steps(self) -> list[str]:
        if self.pseudocode_outline:
            return self.pseudocode_outline
        return self.main_steps

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
