"""Baseline-extraction stage."""

from __future__ import annotations

from pathlib import Path

from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.packages.package_registry import PackageError

from . import prompts
from .ir import AlgorithmSketch
from .runtime import DesignTurnResult, run_design_turn


def run_baseline_analysis(
    *,
    repo_dir: Path,
    goal_spec: dict[str, object],
    goal_rel: str,
    repo_summary: str,
    evidence_summary: str,
    family_catalog: str,
    policy: AgentRuntimePolicy,
    provider_bin_override: str | None,
    output_rel: str,
    runner=run_design_turn,
) -> tuple[AlgorithmSketch, str, DesignTurnResult]:
    prompt = prompts.build_baseline_analysis_prompt(
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        repo_summary=repo_summary,
        evidence_summary=evidence_summary,
        family_catalog=family_catalog,
    )
    instruction_text = prompts.build_design_agents_md(
        goal_rel=goal_rel,
        output_rel=output_rel,
        phase_name="baseline-analysis",
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
            f"baseline analysis failed with exit code {result.return_code}: "
            f"{result.stderr or 'no stderr'}"
        )
    payload = prompts.extract_baseline_analysis(result.assistant_text)
    if not isinstance(payload, dict):
        raise PackageError("baseline analysis did not produce <baseline_analysis> JSON.")
    sketch = AlgorithmSketch.from_payload(
        payload,
        role="baseline",
        hypothesis_id="baseline",
    )
    summary = prompts.extract_analysis_summary(result.assistant_text) or ""
    return sketch, summary, result
