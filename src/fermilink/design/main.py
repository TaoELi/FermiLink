"""Standalone entrypoint for Phase 1 `fermilink design` mode."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fermilink.agent_runtime import (
    AgentRuntimePolicy,
    load_agent_runtime_policy,
    normalize_model,
    normalize_provider,
    normalize_reasoning_effort,
    normalize_sandbox_mode,
    normalize_sandbox_policy,
)
from fermilink.packages.package_registry import PackageError

from . import baseline, evidence, families, goal, render, search, state
from .ir import AlgorithmSketch


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fermilink.design.main",
        description="Phase 1 algorithm-hypothesis search for FermiLink design mode.",
    )
    parser.add_argument("goal_path", help="Path to the structured design goal markdown.")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Local scientific package repo to analyze. Defaults to the current directory.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Optional override for the `.fermilink-design/` output directory.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Override the agent provider. Defaults to the saved FermiLink runtime policy.",
    )
    parser.add_argument(
        "--provider-bin",
        default=None,
        help="Optional provider binary override passed through to the agent runtime.",
    )
    parser.add_argument(
        "--sandbox",
        default=None,
        help="Override the provider sandbox mode for design turns.",
    )
    parser.add_argument(
        "--sandbox-policy",
        default=None,
        help="Override the provider sandbox policy (`enforce` or `bypass`).",
    )
    parser.add_argument("--model", default=None, help="Optional provider model override.")
    parser.add_argument(
        "--reasoning-effort",
        default=None,
        help="Optional provider reasoning effort override.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=12,
        help="Maximum number of candidate hypotheses to request from the provider.",
    )
    parser.add_argument(
        "--shortlist-size",
        type=int,
        default=5,
        help="Number of top-ranked candidates to include in the report.",
    )
    parser.add_argument(
        "--search-profile",
        choices=("conservative", "balanced", "novelty-seeking", "moonshot"),
        default="balanced",
        help=(
            "Ranking preset for archive prioritization. `balanced` preserves the "
            "current conservative default."
        ),
    )
    parser.add_argument(
        "--baseline-publications",
        action="store_true",
        help=(
            "Run an additional publication-check turn after the mandatory "
            "baseline audit. Code remains authoritative if literature disagrees."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse existing baseline and archive artifacts when present.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a final JSON payload instead of concise human-readable lines.",
    )
    return parser


def _resolve_policy(args: argparse.Namespace) -> AgentRuntimePolicy:
    base = load_agent_runtime_policy()
    return AgentRuntimePolicy(
        provider=normalize_provider(args.provider or base.provider),
        sandbox_policy=normalize_sandbox_policy(
            args.sandbox_policy or base.sandbox_policy
        ),
        sandbox_mode=normalize_sandbox_mode(args.sandbox or base.sandbox_mode),
        model=normalize_model(args.model if args.model is not None else base.model),
        reasoning_effort=normalize_reasoning_effort(
            args.reasoning_effort
            if args.reasoning_effort is not None
            else base.reasoning_effort
        ),
    )


def _resolve_goal_rel(goal_path: Path, project_root: Path) -> str:
    try:
        return goal_path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(goal_path.resolve())


def _read_optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = state.read_json(path)
    return payload if isinstance(payload, dict) else {}


def _publication_unavailable_payload(reason: str) -> dict[str, Any]:
    return {
        "status": "unavailable",
        "internet_used": False,
        "references": [],
        "publication_support": [],
        "publication_conflicts": [],
        "canonical_terms": [],
        "evidence_gaps": [str(reason or "publication retrieval unavailable")],
        "publication_summary": (
            "Publication check could not complete. Continue using the "
            "audited code-grounded baseline."
        ),
    }


def _materialize_baseline(
    *,
    paths: state.DesignPaths,
    goal_spec: dict[str, Any],
    goal_rel: str,
    repo_summary: str,
    evidence_summary: str,
    family_catalog: str,
    policy: AgentRuntimePolicy,
    provider_bin_override: str | None,
    resume: bool,
    enable_publication_check: bool,
) -> tuple[AlgorithmSketch, str]:
    output_rel = (
        paths.state_root.relative_to(paths.project_root).as_posix()
        if paths.state_root.is_relative_to(paths.project_root)
        else str(paths.state_root)
    )

    extractor_payload = _read_optional_json(paths.baseline_extractor_path) if resume else {}
    extractor_summary = str(extractor_payload.get("analysis_summary") or "")
    raw_extractor_sketch = extractor_payload.get("sketch")
    if isinstance(raw_extractor_sketch, dict):
        extracted_sketch = AlgorithmSketch.from_payload(
            raw_extractor_sketch,
            role="baseline",
            hypothesis_id="baseline",
        )
    else:
        extracted_sketch, extractor_summary, _ = baseline.run_baseline_analysis(
            repo_dir=paths.project_root,
            goal_spec=goal_spec,
            goal_rel=goal_rel,
            repo_summary=repo_summary,
            evidence_summary=evidence_summary,
            family_catalog=family_catalog,
            policy=policy,
            provider_bin_override=provider_bin_override,
            output_rel=output_rel,
        )
        extractor_payload = {
            "sketch": extracted_sketch.to_dict(),
            "analysis_summary": extractor_summary,
        }
        state.write_json(paths.baseline_extractor_path, extractor_payload)

    audit_payload = _read_optional_json(paths.baseline_audit_path) if resume else {}
    audit_summary = str(audit_payload.get("audit_summary") or "")
    raw_audited_sketch = audit_payload.get("resolved_sketch")
    if isinstance(raw_audited_sketch, dict):
        audited_sketch = AlgorithmSketch.from_payload(
            raw_audited_sketch,
            role="baseline",
            hypothesis_id="baseline",
        )
    else:
        audited_sketch, audit_payload, audit_summary, _ = baseline.run_baseline_audit(
            repo_dir=paths.project_root,
            goal_spec=goal_spec,
            goal_rel=goal_rel,
            extracted_sketch=extracted_sketch,
            repo_summary=repo_summary,
            evidence_summary=evidence_summary,
            policy=policy,
            provider_bin_override=provider_bin_override,
            output_rel=output_rel,
        )
        audit_payload = dict(audit_payload)
        audit_payload["resolved_sketch"] = audited_sketch.to_dict()
        audit_payload["audit_summary"] = audit_summary
        state.write_json(paths.baseline_audit_path, audit_payload)

    publication_payload: dict[str, Any] = {}
    publication_summary = ""
    if enable_publication_check:
        publication_payload = (
            _read_optional_json(paths.baseline_publication_path) if resume else {}
        )
        publication_summary = str(publication_payload.get("publication_summary") or "")
        if not publication_payload:
            try:
                publication_payload, publication_summary, _ = baseline.run_publication_check(
                    repo_dir=paths.project_root,
                    goal_spec=goal_spec,
                    goal_rel=goal_rel,
                    audited_sketch=audited_sketch,
                    audit_payload=audit_payload,
                    evidence_summary=evidence_summary,
                    policy=policy,
                    provider_bin_override=provider_bin_override,
                    output_rel=output_rel,
                )
                publication_payload = dict(publication_payload)
                publication_payload["publication_summary"] = publication_summary
            except PackageError as exc:
                publication_payload = _publication_unavailable_payload(str(exc))
                publication_summary = str(
                    publication_payload.get("publication_summary") or ""
                )
            state.write_json(paths.baseline_publication_path, publication_payload)

    summary = audit_summary or extractor_summary
    state.write_json(paths.baseline_sketch_path, audited_sketch.to_dict())
    state.write_text(
        paths.baseline_report_path,
        render.render_baseline_report(
            audited_sketch,
            summary,
            extractor_summary=extractor_summary,
            audit_payload=audit_payload,
            audit_summary=audit_summary,
            publication_payload=publication_payload,
            publication_summary=publication_summary,
        ),
    )
    return audited_sketch, summary


def _materialize_archive(
    *,
    paths: state.DesignPaths,
    goal_spec: dict[str, Any],
    goal_rel: str,
    baseline_sketch: AlgorithmSketch,
    repo_summary: str,
    evidence_summary: str,
    local_corpus: str,
    family_catalog: str,
    max_candidates: int,
    shortlist_size: int,
    search_profile: str,
    policy: AgentRuntimePolicy,
    provider_bin_override: str | None,
    resume: bool,
    baseline_summary: str,
    baseline_publications: bool,
) -> dict[str, Any]:
    if resume and paths.summary_json_path.exists():
        return state.read_json(paths.summary_json_path)

    candidates, search_summary, _ = search.run_candidate_search(
        repo_dir=paths.project_root,
        goal_spec=goal_spec,
        baseline=baseline_sketch,
        repo_summary=repo_summary,
        evidence_summary=evidence_summary,
        family_catalog=family_catalog,
        max_candidates=max_candidates,
        search_profile=search_profile,
        policy=policy,
        provider_bin_override=provider_bin_override,
        goal_rel=goal_rel,
        output_rel=paths.state_root.relative_to(paths.project_root).as_posix()
        if paths.state_root.is_relative_to(paths.project_root)
        else str(paths.state_root),
    )
    archive_rows = search.build_ranked_archive(
        candidates,
        baseline=baseline_sketch,
        goal_spec=goal_spec,
        local_corpus=local_corpus,
        search_profile=search_profile,
    )
    state.append_jsonl(paths.archive_jsonl_path, archive_rows)

    for row in archive_rows:
        sketch = AlgorithmSketch.from_payload(row["sketch"], role="candidate")
        hypothesis_id = sketch.hypothesis_id
        state.write_json(paths.candidate_sketch_path(hypothesis_id), sketch.to_dict())
        state.write_text(
            paths.candidate_pseudocode_path(hypothesis_id),
            render.render_candidate_pseudocode(
                sketch,
                score=float(row.get("score") or 0.0),
                novelty=row.get("novelty") or {},
                baseline=baseline_sketch,
            ),
        )
        state.write_text(
            paths.candidate_novelty_path(hypothesis_id),
            render.render_novelty_report(row.get("novelty") or {}),
        )
        state.write_text(
            paths.candidate_comparison_path(hypothesis_id),
            render.render_candidate_comparison(
                sketch,
                score_breakdown=row.get("score_breakdown") or {},
                novelty=row.get("novelty") or {},
                baseline=baseline_sketch,
            ),
        )

    shortlist_report = render.render_shortlist_report(
        goal_spec=goal_spec,
        baseline=baseline_sketch,
        archive_rows=archive_rows,
        shortlist_size=shortlist_size,
        baseline_summary=baseline_summary if isinstance(baseline_summary, str) else "",
        search_summary=search_summary,
        search_profile=search_profile,
    )
    state.write_text(paths.shortlist_report_path, shortlist_report)
    summary = {
        "project_root": str(paths.project_root),
        "goal_path": goal_rel,
        "baseline_hypothesis_id": baseline_sketch.hypothesis_id,
        "search_profile": search_profile,
        "baseline_publications": bool(baseline_publications),
        "shortlist_report_path": str(paths.shortlist_report_path),
        "baseline_report_path": str(paths.baseline_report_path),
        "archive_jsonl_path": str(paths.archive_jsonl_path),
        "shortlist": archive_rows[:shortlist_size],
    }
    state.write_json(paths.summary_json_path, summary)
    return summary


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    goal_path = Path(args.goal_path).expanduser().resolve()
    if not goal_path.is_file():
        raise PackageError(f"Goal file not found: {goal_path}")
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise PackageError(f"Project root not found: {project_root}")

    goal_text = goal_path.read_text(encoding="utf-8")
    goal_spec = goal.parse_goal(goal_text)
    if not goal.is_design_goal_markdown(goal_text):
        raise PackageError(
            "Goal markdown does not look like a structured design goal. "
            "Include headings such as `## Scientific Problem`, `## Target Kernel`, "
            "or `## Target Regime`."
        )

    policy = _resolve_policy(args)
    output_root = (
        Path(args.output_root).expanduser().resolve()
        if isinstance(args.output_root, str) and args.output_root.strip()
        else None
    )
    paths = state.resolve_design_paths(project_root, output_root=output_root)
    state.ensure_design_dirs(paths)

    goal_rel = _resolve_goal_rel(goal_path, project_root)
    family_library = families.load_family_library()
    family_catalog = families.render_family_catalog(family_library)
    repo_summary = evidence.summarize_repo(project_root)
    evidence_bundle = evidence.collect_evidence_bundle(project_root, goal_spec)
    evidence_summary = evidence.render_evidence_summary(evidence_bundle)
    local_corpus = evidence.build_local_corpus(goal_spec, evidence_bundle)

    state.write_json(paths.goal_json_path, goal_spec)
    state.write_json(paths.evidence_manifest_path, evidence_bundle)

    baseline_sketch, baseline_summary = _materialize_baseline(
        paths=paths,
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        repo_summary=repo_summary,
        evidence_summary=evidence_summary,
        family_catalog=family_catalog,
        policy=policy,
        provider_bin_override=args.provider_bin,
        resume=bool(args.resume),
        enable_publication_check=bool(args.baseline_publications),
    )
    summary = _materialize_archive(
        paths=paths,
        goal_spec=goal_spec,
        goal_rel=goal_rel,
        baseline_sketch=baseline_sketch,
        repo_summary=repo_summary,
        evidence_summary=evidence_summary,
        local_corpus=local_corpus,
        family_catalog=family_catalog,
        max_candidates=max(1, int(args.max_candidates)),
        shortlist_size=max(1, int(args.shortlist_size)),
        search_profile=str(args.search_profile or "balanced"),
        policy=policy,
        provider_bin_override=args.provider_bin,
        resume=bool(args.resume),
        baseline_summary=baseline_summary,
        baseline_publications=bool(args.baseline_publications),
    )
    summary["search_profile"] = str(args.search_profile or "balanced")
    summary["baseline_publications"] = bool(args.baseline_publications)
    if bool(args.baseline_publications) and paths.baseline_publication_path.exists():
        summary["baseline_publication_check_path"] = str(paths.baseline_publication_path)

    manifest_path = paths.new_session_manifest_path()
    state.write_json(
        manifest_path,
        {
            "goal_path": goal_rel,
            "search_profile": str(args.search_profile or "balanced"),
            "baseline_publications": bool(args.baseline_publications),
            "policy": policy.as_dict(),
            "summary_path": str(paths.summary_json_path),
        },
    )
    summary["session_manifest_path"] = str(manifest_path)
    state.write_json(paths.summary_json_path, summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        payload = run_pipeline(args)
    except PackageError as exc:
        print(f"[design] {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[design] interrupted by user.", file=sys.stderr)
        return 130

    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    for line in build_summary_lines(payload):
        print(line)
    return 0


def build_summary_lines(payload: dict[str, Any]) -> list[str]:
    shortlist = payload.get("shortlist") or []
    lines = [
        f"[design] shortlist report: {payload.get('shortlist_report_path')}",
        f"[design] baseline report: {payload.get('baseline_report_path')}",
        f"[design] archive ledger: {payload.get('archive_jsonl_path')}",
        f"[design] search profile: {payload.get('search_profile', 'balanced')}",
        f"[design] baseline publications: {bool(payload.get('baseline_publications'))}",
        f"[design] shortlisted hypotheses: {len(shortlist)}",
    ]
    publication_path = payload.get("baseline_publication_check_path")
    if publication_path:
        lines.append(f"[design] publication check: {publication_path}")
    for item in shortlist[:5]:
        sketch = item.get("sketch") or {}
        novelty = item.get("novelty") or {}
        lines.append(
            "[design] "
            f"rank {item.get('rank')}: {sketch.get('title')} "
            f"(score={float(item.get('score') or 0.0):.4f}, novelty={novelty.get('label')})"
        )
    return lines
