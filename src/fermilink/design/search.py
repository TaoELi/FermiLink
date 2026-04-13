"""Candidate-search stage and archive construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.packages.package_registry import PackageError

from . import prompts
from .ir import AlgorithmSketch
from .novelty import assess_candidate_novelty
from .runtime import DesignTurnResult, run_design_turn
from .scoring import score_candidate


def run_candidate_search(
    *,
    repo_dir: Path,
    goal_spec: dict[str, Any],
    baseline: AlgorithmSketch,
    repo_summary: str,
    evidence_summary: str,
    family_catalog: str,
    max_candidates: int,
    search_profile: str,
    policy: AgentRuntimePolicy,
    provider_bin_override: str | None,
    goal_rel: str,
    output_rel: str,
    runner=run_design_turn,
) -> tuple[list[AlgorithmSketch], str, DesignTurnResult]:
    prompt = prompts.build_candidate_search_prompt(
        goal_spec=goal_spec,
        baseline_sketch=baseline.to_dict(),
        repo_summary=repo_summary,
        evidence_summary=evidence_summary,
        family_catalog=family_catalog,
        max_candidates=max_candidates,
        search_profile=search_profile,
    )
    instruction_text = prompts.build_design_agents_md(
        goal_rel=goal_rel,
        output_rel=output_rel,
        phase_name="candidate-search",
    )
    result = runner(
        repo_dir=repo_dir,
        prompt=prompt,
        policy=policy,
        instruction_text=instruction_text,
        provider_bin_override=provider_bin_override,
    )
    if result.return_code != 0:
        raise PackageError(
            f"candidate search failed with exit code {result.return_code}: "
            f"{result.stderr or 'no stderr'}"
        )
    payload = prompts.extract_candidate_archive(result.assistant_text)
    if not isinstance(payload, dict):
        raise PackageError("candidate search did not produce <candidate_archive> JSON.")
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise PackageError("candidate search returned no candidates.")
    sketches: list[AlgorithmSketch] = []
    for item in raw_candidates[:max_candidates]:
        if not isinstance(item, dict):
            continue
        sketches.append(
            AlgorithmSketch.from_payload(
                item,
                role="candidate",
                parent_id=baseline.hypothesis_id,
            )
        )
    summary = prompts.extract_search_summary(result.assistant_text) or ""
    return sketches, summary, result


def build_ranked_archive(
    candidates: list[AlgorithmSketch],
    *,
    baseline: AlgorithmSketch,
    goal_spec: dict[str, Any],
    local_corpus: str,
    search_profile: str = "balanced",
) -> list[dict[str, Any]]:
    deduped: dict[str, AlgorithmSketch] = {}
    for candidate in candidates:
        signature = candidate.canonical_signature() or candidate.hypothesis_id
        incumbent = deduped.get(signature)
        if incumbent is None or candidate.confidence > incumbent.confidence:
            deduped[signature] = candidate

    ranked: list[dict[str, Any]] = []
    for candidate in deduped.values():
        novelty = assess_candidate_novelty(
            candidate,
            baseline=baseline,
            local_corpus=local_corpus,
        )
        scores = score_candidate(
            candidate,
            baseline=baseline,
            goal_spec=goal_spec,
            novelty_label=novelty.label,
            search_profile=search_profile,
        )
        ranked.append(
            {
                "hypothesis_id": candidate.hypothesis_id,
                "signature": candidate.canonical_signature(),
                "score": scores["total"],
                "search_profile": search_profile,
                "score_breakdown": scores,
                "novelty": novelty.to_dict(),
                "sketch": candidate.to_dict(),
            }
        )
    ranked.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
    return ranked
