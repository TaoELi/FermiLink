"""Prompt templates and extraction helpers for design mode."""

from __future__ import annotations

import json
import re
from typing import Any

from fermilink.cli.workflow_prompts import LOOP_DONE_TOKEN


BASELINE_ANALYSIS_TAG = "baseline_analysis"
BASELINE_AUDIT_TAG = "baseline_audit"
PUBLICATION_CHECK_TAG = "publication_check"
CANDIDATE_ARCHIVE_TAG = "candidate_archive"
ANALYSIS_SUMMARY_TAG = "analysis_summary"
AUDIT_SUMMARY_TAG = "audit_summary"
PUBLICATION_SUMMARY_TAG = "publication_summary"
SEARCH_SUMMARY_TAG = "search_summary"

BASELINE_ANALYSIS_RE = re.compile(
    rf"<{BASELINE_ANALYSIS_TAG}>\s*(.*?)\s*</{BASELINE_ANALYSIS_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
CANDIDATE_ARCHIVE_RE = re.compile(
    rf"<{CANDIDATE_ARCHIVE_TAG}>\s*(.*?)\s*</{CANDIDATE_ARCHIVE_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
BASELINE_AUDIT_RE = re.compile(
    rf"<{BASELINE_AUDIT_TAG}>\s*(.*?)\s*</{BASELINE_AUDIT_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
PUBLICATION_CHECK_RE = re.compile(
    rf"<{PUBLICATION_CHECK_TAG}>\s*(.*?)\s*</{PUBLICATION_CHECK_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
ANALYSIS_SUMMARY_RE = re.compile(
    rf"<{ANALYSIS_SUMMARY_TAG}>\s*(.*?)\s*</{ANALYSIS_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
AUDIT_SUMMARY_RE = re.compile(
    rf"<{AUDIT_SUMMARY_TAG}>\s*(.*?)\s*</{AUDIT_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
PUBLICATION_SUMMARY_RE = re.compile(
    rf"<{PUBLICATION_SUMMARY_TAG}>\s*(.*?)\s*</{PUBLICATION_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)
SEARCH_SUMMARY_RE = re.compile(
    rf"<{SEARCH_SUMMARY_TAG}>\s*(.*?)\s*</{SEARCH_SUMMARY_TAG}>",
    re.IGNORECASE | re.DOTALL,
)


def _extract_json(text: str, pattern: re.Pattern[str]) -> dict[str, Any] | None:
    match = pattern.search(str(text or ""))
    if not match:
        return None
    raw = match.group(1).strip()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _extract_text(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(str(text or ""))
    if not match:
        return None
    value = match.group(1).strip()
    return value or None


def extract_baseline_analysis(text: str) -> dict[str, Any] | None:
    return _extract_json(text, BASELINE_ANALYSIS_RE)


def extract_candidate_archive(text: str) -> dict[str, Any] | None:
    return _extract_json(text, CANDIDATE_ARCHIVE_RE)


def extract_baseline_audit(text: str) -> dict[str, Any] | None:
    return _extract_json(text, BASELINE_AUDIT_RE)


def extract_publication_check(text: str) -> dict[str, Any] | None:
    return _extract_json(text, PUBLICATION_CHECK_RE)


def extract_analysis_summary(text: str) -> str | None:
    return _extract_text(text, ANALYSIS_SUMMARY_RE)


def extract_audit_summary(text: str) -> str | None:
    return _extract_text(text, AUDIT_SUMMARY_RE)


def extract_publication_summary(text: str) -> str | None:
    return _extract_text(text, PUBLICATION_SUMMARY_RE)


def extract_search_summary(text: str) -> str | None:
    return _extract_text(text, SEARCH_SUMMARY_RE)


def build_design_agents_md(
    *,
    goal_rel: str,
    output_rel: str,
    phase_name: str,
) -> str:
    return (
        "# FermiLink Design Mode\n"
        "\n"
        f"You are running the `{phase_name}` phase of FermiLink design mode.\n"
        "\n"
        "Read these first:\n"
        f"- `{goal_rel}`\n"
        "- Relevant source files in the target repository\n"
        "- Local docs, README files, and evidence files referenced by the goal\n"
        "\n"
        "You may read any file in this repository.\n"
        f"You may only write to `{output_rel}`.\n"
        "\n"
        "Do not modify source code.\n"
        "Do not claim proven novelty.\n"
        "Describe outputs as candidate algorithm hypotheses with evidence.\n"
    )


def _json_contract() -> str:
    return (
        "{\n"
        '  "title": "...",\n'
        '  "problem": "...",\n'
        '  "family": "...",\n'
        '  "mechanism": "...",\n'
        '  "target_regime": "...",\n'
        '  "dominant_kernel": "...",\n'
        '  "data_representation": "...",\n'
        '  "main_steps": ["..."],\n'
        '  "bottlenecks": ["..."],\n'
        '  "time_complexity": "...",\n'
        '  "memory_complexity": "...",\n'
        '  "communication_complexity": "...",\n'
        '  "expected_advantages": ["..."],\n'
        '  "expected_disadvantages": ["..."],\n'
        '  "required_invariants": ["..."],\n'
        '  "accuracy_controls": ["..."],\n'
        '  "failure_modes": ["..."],\n'
        '  "novelty_signature": ["..."],\n'
        '  "pseudocode_outline": ["..."],\n'
        '  "scientific_rationale": "...",\n'
        '  "predicted_gain_score": 0.0,\n'
        '  "regime_fit_score": 0.0,\n'
        '  "feasibility_score": 0.0,\n'
        '  "novelty_score": 0.0,\n'
        '  "risk_score": 0.0,\n'
        '  "confidence": 0.0\n'
        "}"
    )


def _audit_contract() -> str:
    return (
        "{\n"
        '  "resolved_sketch": { ... AlgorithmSketch schema ... },\n'
        '  "field_verdicts": [\n'
        "    {\n"
        '      "field": "family",\n'
        '      "status": "confirmed_by_code|supported_by_local_docs|contradicted|uncertain",\n'
        '      "evidence": "...",\n'
        '      "source_paths": ["path/to/file"]\n'
        "    }\n"
        "  ],\n"
        '  "disagreements": [\n'
        "    {\n"
        '      "field": "mechanism",\n'
        '      "extractor_claim": "...",\n'
        '      "audit_finding": "...",\n'
        '      "basis": "...",\n'
        '      "source_paths": ["path/to/file"]\n'
        "    }\n"
        "  ],\n"
        '  "uncertainties": ["..."],\n'
        '  "source_paths": ["path/to/file"]\n'
        "}"
    )


def _publication_contract() -> str:
    return (
        "{\n"
        '  "internet_used": true,\n'
        '  "references": [\n'
        "    {\n"
        '      "title": "...",\n'
        '      "url": "https://...",\n'
        '      "type": "paper|docs|repo|other",\n'
        '      "relevance": "..."\n'
        "    }\n"
        "  ],\n"
        '  "publication_support": [\n'
        "    {\n"
        '      "field": "family",\n'
        '      "status": "supported_by_publication|terminology_alignment|no_match_found",\n'
        '      "note": "...",\n'
        '      "reference_title": "..."\n'
        "    }\n"
        "  ],\n"
        '  "publication_conflicts": [\n'
        "    {\n"
        '      "field": "mechanism",\n'
        '      "publication_claim": "...",\n'
        '      "code_finding": "...",\n'
        '      "resolution": "prefer_code",\n'
        '      "reference_title": "..."\n'
        "    }\n"
        "  ],\n"
        '  "canonical_terms": ["..."],\n'
        '  "evidence_gaps": ["..."]\n'
        "}"
    )


def _candidate_profile_guidance(search_profile: str) -> str:
    profile = str(search_profile or "balanced").strip().casefold()
    if profile == "conservative":
        return (
            "- Prioritize feasible, low-risk hypotheses that stay close to the baseline family.\n"
            "- Spend most of the candidate budget on exact or near-exact reformulations, caching, blocking, reuse, or other low-risk structural improvements.\n"
            "- Include at most one bolder family-changing idea if it is still scientifically defensible.\n"
        )
    if profile == "novelty-seeking":
        return (
            "- Prioritize mechanistically distinct hypotheses with explicit large-regime advantages.\n"
            "- Allocate at least half of the candidate budget to family-changing hypotheses rather than same-family refinements.\n"
            "- If you include same-family ideas, keep only the strongest ones and make their limitation explicit.\n"
        )
    if profile == "moonshot":
        return (
            "- Prioritize bold, family-changing hypotheses with potentially large asymptotic or extreme-regime upside.\n"
            "- Allocate most of the candidate budget to structurally different families, even when feasibility is less certain.\n"
            "- Include only a small number of conservative variants, if any, and clearly separate speculative ideas from credible ones.\n"
        )
    return (
        "- Balance feasible near-term gains against mechanistically distinct directions.\n"
        "- Allocate meaningful candidate budget to family-changing ideas instead of spending the full budget on safe same-family variants.\n"
        "- Keep at least one strong candidate that is structurally distinct from the baseline when credible local evidence supports it.\n"
    )


def build_baseline_analysis_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    repo_summary: str,
    evidence_summary: str,
    family_catalog: str,
) -> str:
    return (
        "You are reconstructing the current baseline algorithm for FermiLink "
        "design mode.\n"
        "\n"
        "Treat the deliverable as a typed algorithm sketch, not free-form prose.\n"
        "State what the current implementation likely does, its dominant "
        "kernel, the limiting regime, and where the runtime cost concentrates.\n"
        "\n"
        f"## Goal file\nPath: `{goal_rel}`\n"
        "```\n"
        f"{goal_spec.get('raw_text', '')}\n"
        "```\n"
        "\n"
        "## Repository file listing\n"
        f"{repo_summary}\n"
        "\n"
        "## Local evidence excerpts\n"
        f"{evidence_summary}\n"
        "\n"
        "## Family library reference\n"
        f"{family_catalog}\n"
        "\n"
        "## Output requirements\n"
        f"- Emit one JSON object inside <{BASELINE_ANALYSIS_TAG}> tags.\n"
        f"- Use this schema:\n```\n{_json_contract()}\n```\n"
        f"- Emit a concise narrative inside <{ANALYSIS_SUMMARY_TAG}> tags.\n"
        "- Keep novelty-related scores conservative for the baseline.\n"
        "- The sketch must reflect the existing algorithm, not a proposal.\n"
        "- Include pseudocode steps that a scientist can read quickly.\n"
        "- Explicitly identify the most time-consuming or scaling-limiting part.\n"
        "\n"
        "End with:\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_candidate_search_prompt(
    *,
    goal_spec: dict[str, Any],
    baseline_sketch: dict[str, Any],
    repo_summary: str,
    evidence_summary: str,
    family_catalog: str,
    max_candidates: int,
    search_profile: str = "balanced",
) -> str:
    return (
        "You are proposing algorithm-hypothesis candidates for FermiLink design "
        "mode Phase 1.\n"
        "\n"
        "Search over typed scientific-computing transformations, not arbitrary "
        "open-ended inventions. Favor mechanism changes with explicit regime "
        "advantages.\n"
        "\n"
        "## Goal\n"
        "```\n"
        f"{goal_spec.get('raw_text', '')}\n"
        "```\n"
        "\n"
        "## Baseline sketch\n"
        "```json\n"
        f"{json.dumps(baseline_sketch, indent=2, sort_keys=True)}\n"
        "```\n"
        "\n"
        "## Repository file listing\n"
        f"{repo_summary}\n"
        "\n"
        "## Local evidence excerpts\n"
        f"{evidence_summary}\n"
        "\n"
        "## Family library\n"
        f"{family_catalog}\n"
        "\n"
        "## Search profile guidance\n"
        f"Selected profile: `{search_profile}`\n"
        f"{_candidate_profile_guidance(search_profile)}"
        "\n"
        "## Search rules\n"
        "- Propose candidate algorithm hypotheses, not implementation patches.\n"
        "- Every candidate must identify one dominant mechanism and one target "
        "regime.\n"
        "- Reject trivial variants that merely retune thresholds or rename a "
        "known method.\n"
        "- Prefer candidates whose crossover regime is explicit, such as "
        "`worse below size X, better above size X`.\n"
        "- Be conservative about novelty claims. Use `novelty_score` as a "
        "hypothesis-confidence signal, not proof.\n"
        "\n"
        "## Output requirements\n"
        f"- Emit one JSON object inside <{CANDIDATE_ARCHIVE_TAG}> tags.\n"
        f"- The object must have keys `candidates` and `rejected_ideas`.\n"
        f"- Emit at most {max_candidates} candidates.\n"
        "- Each candidate must follow this schema:\n"
        f"```\n{_json_contract()}\n```\n"
        f"- Emit a concise search rationale inside <{SEARCH_SUMMARY_TAG}> tags.\n"
        "\n"
        "End with:\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_baseline_audit_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    extracted_sketch: dict[str, Any],
    repo_summary: str,
    evidence_summary: str,
) -> str:
    return (
        "You are independently auditing the extracted baseline algorithm for "
        "FermiLink design mode.\n"
        "\n"
        "Authority order is strict:\n"
        "1. Current source code and tests\n"
        "2. Local docs and README files\n"
        "3. External publications are not part of this audit step\n"
        "\n"
        "Correct the extracted baseline if it does not match the implementation. "
        "Do not preserve a mistaken baseline for consistency.\n"
        "\n"
        f"## Goal file\nPath: `{goal_rel}`\n"
        "```\n"
        f"{goal_spec.get('raw_text', '')}\n"
        "```\n"
        "\n"
        "## Extracted baseline sketch\n"
        "```json\n"
        f"{json.dumps(extracted_sketch, indent=2, sort_keys=True)}\n"
        "```\n"
        "\n"
        "## Repository file listing\n"
        f"{repo_summary}\n"
        "\n"
        "## Local evidence excerpts\n"
        f"{evidence_summary}\n"
        "\n"
        "## Audit rules\n"
        "- Verify that family, mechanism, dominant kernel, bottlenecks, and pseudocode actually match the current implementation.\n"
        "- Use tests as implementation evidence when helpful.\n"
        "- Downgrade unsupported complexity claims to `uncertain` rather than inventing certainty.\n"
        "- Record disagreements explicitly instead of smoothing them over.\n"
        "- Use `confirmed_by_code`, `supported_by_local_docs`, `contradicted`, or `uncertain` in field verdicts.\n"
        "\n"
        "## Output requirements\n"
        f"- Emit one JSON object inside <{BASELINE_AUDIT_TAG}> tags.\n"
        f"- Use this schema:\n```\n{_audit_contract()}\n```\n"
        f"- `resolved_sketch` must follow this schema:\n```\n{_json_contract()}\n```\n"
        f"- Emit a concise audit rationale inside <{AUDIT_SUMMARY_TAG}> tags.\n"
        "\n"
        "End with:\n"
        f"{LOOP_DONE_TOKEN}\n"
    )


def build_publication_check_prompt(
    *,
    goal_spec: dict[str, Any],
    goal_rel: str,
    audited_sketch: dict[str, Any],
    audit_payload: dict[str, Any],
    evidence_summary: str,
) -> str:
    prior_art_hints = goal_spec.get("prior_art_hints") or []
    prior_art_text = (
        "\n".join(f"- {item}" for item in prior_art_hints if str(item).strip())
        if isinstance(prior_art_hints, list) and prior_art_hints
        else "- (none provided)"
    )
    return (
        "You are checking external publications and canonical references for the "
        "audited baseline algorithm in FermiLink design mode.\n"
        "\n"
        "Authority order is strict:\n"
        "1. Current source code and tests\n"
        "2. Local docs and README files\n"
        "3. External publications and public references\n"
        "\n"
        "Use external internet search only if your provider/runtime supports it. "
        "If internet access is unavailable, say so explicitly and do not invent citations.\n"
        "When publications disagree with the current code, preserve the disagreement "
        "explicitly and prefer the audited code-grounded baseline.\n"
        "\n"
        f"## Goal file\nPath: `{goal_rel}`\n"
        "```\n"
        f"{goal_spec.get('raw_text', '')}\n"
        "```\n"
        "\n"
        "## Audited baseline sketch\n"
        "```json\n"
        f"{json.dumps(audited_sketch, indent=2, sort_keys=True)}\n"
        "```\n"
        "\n"
        "## Audit payload\n"
        "```json\n"
        f"{json.dumps(audit_payload, indent=2, sort_keys=True)}\n"
        "```\n"
        "\n"
        "## Local evidence excerpts\n"
        f"{evidence_summary}\n"
        "\n"
        "## Prior-art hints\n"
        f"{prior_art_text}\n"
        "\n"
        "## Output requirements\n"
        f"- Emit one JSON object inside <{PUBLICATION_CHECK_TAG}> tags.\n"
        f"- Use this schema:\n```\n{_publication_contract()}\n```\n"
        f"- Emit a concise publication-check rationale inside <{PUBLICATION_SUMMARY_TAG}> tags.\n"
        "- Do not modify the audited baseline in this step.\n"
        "- Only report references you genuinely found. Leave references empty if unavailable.\n"
        "\n"
        "End with:\n"
        f"{LOOP_DONE_TOKEN}\n"
    )
