from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.drvloop.prompts import (
    DRVLOOP_STATE_DIRNAME,
    DRVLOOP_VALIDATION_REPORT_FILENAME,
    DRVLOOP_WORKFLOW_FILENAME,
)
from fermilink.drvloop.spec import DerivationSpecContext


SUPPORTED_PROOF_DEPTHS = ("quick", "standard", "publication")
_TEXT_SUFFIXES = {".md", ".tex", ".rst"}
_SCRIPT_SUFFIXES = {".py", ".jl", ".m", ".wl", ".nb", ".ipynb"}
_DISPLAY_MATH_RE = re.compile(
    r"(\$\$.*?\$\$|\\\[.*?\\\]|\\begin\{(?:equation|align)\*?\}.*?\\end\{(?:equation|align)\*?\})",
    re.DOTALL,
)
_ROUTE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:#+\s*)?(?:pathway|route)\s*[-_ #:]*(\d+|[A-Za-z])\b",
    re.IGNORECASE,
)
_MEMORY_ROUND_RE = re.compile(r"\bRound\s+(\d+)\b", re.IGNORECASE)
_WEAK_TARGET_TYPES = {
    "citation",
    "literature",
    "assumption",
    "approximation",
    "manual",
    "review",
    "final_artifact",
    "final_manuscript",
    "pedagogical_note",
    "latex_build",
    "latex",
}


@dataclass(frozen=True)
class WorkflowProfile:
    name: str
    min_iterations: int
    min_route_candidates: int
    required_pathways: int
    min_developed_route_lines: int
    min_manuscript_lines: int
    min_manuscript_equations: int
    min_note_lines: int
    min_explicit_obligations: int
    min_strong_target_obligations: int
    require_route_ranking: bool
    require_synthesis: bool
    require_gap_review: bool
    require_numerical_checks: bool
    require_final_consistency_review: bool


_PROFILES: dict[str, WorkflowProfile] = {
    "quick": WorkflowProfile(
        name="quick",
        min_iterations=1,
        min_route_candidates=0,
        required_pathways=0,
        min_developed_route_lines=1,
        min_manuscript_lines=1,
        min_manuscript_equations=0,
        min_note_lines=1,
        min_explicit_obligations=1,
        min_strong_target_obligations=1,
        require_route_ranking=False,
        require_synthesis=False,
        require_gap_review=False,
        require_numerical_checks=False,
        require_final_consistency_review=False,
    ),
    "standard": WorkflowProfile(
        name="standard",
        min_iterations=4,
        min_route_candidates=4,
        required_pathways=3,
        min_developed_route_lines=50,
        min_manuscript_lines=160,
        min_manuscript_equations=6,
        min_note_lines=60,
        min_explicit_obligations=6,
        min_strong_target_obligations=2,
        require_route_ranking=True,
        require_synthesis=True,
        require_gap_review=True,
        require_numerical_checks=False,
        require_final_consistency_review=True,
    ),
    "publication": WorkflowProfile(
        name="publication",
        min_iterations=10,
        min_route_candidates=10,
        required_pathways=10,
        min_developed_route_lines=80,
        min_manuscript_lines=500,
        min_manuscript_equations=12,
        min_note_lines=120,
        min_explicit_obligations=10,
        min_strong_target_obligations=3,
        require_route_ranking=True,
        require_synthesis=True,
        require_gap_review=True,
        require_numerical_checks=True,
        require_final_consistency_review=True,
    ),
}


def normalize_proof_depth(value: str | None) -> str:
    depth = str(value or "publication").strip().lower()
    if depth not in _PROFILES:
        return "publication"
    return depth


def workflow_state_path_for(repo_dir: Path) -> Path:
    return repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_WORKFLOW_FILENAME


def evaluate_drvloop_workflow(
    *,
    repo_dir: Path,
    spec_context: DerivationSpecContext,
    validation_report: dict[str, Any],
    proof_depth: str,
    iteration: int,
) -> dict[str, Any]:
    """Evaluate publication-process gates and persist workflow state."""

    profile = _PROFILES[normalize_proof_depth(proof_depth)]
    project_path = repo_dir / spec_context.project_rel
    artifacts = _scan_project_artifacts(repo_dir, project_path)
    metrics = _build_metrics(
        repo_dir=repo_dir,
        artifacts=artifacts,
        validation_report=validation_report,
        profile=profile,
    )
    effective_iteration = max(
        int(iteration),
        _observed_drvloop_round_count(repo_dir),
    )
    stages = _build_stages(metrics, validation_report, profile, effective_iteration)
    summary = _summarize_workflow(stages, metrics, validation_report, profile)
    state = {
        "schema_version": 1,
        "generated_at_utc": _utc_now_z(),
        "proof_depth": profile.name,
        "project": spec_context.project_rel,
        "spec": spec_context.spec_rel,
        "current_iteration": effective_iteration,
        "profile": {
            "min_iterations": profile.min_iterations,
            "min_route_candidates": profile.min_route_candidates,
            "required_pathways": profile.required_pathways,
            "min_manuscript_lines": profile.min_manuscript_lines,
            "min_note_lines": profile.min_note_lines,
            "min_explicit_obligations": profile.min_explicit_obligations,
            "min_strong_target_obligations": profile.min_strong_target_obligations,
        },
        "metrics": metrics,
        "stages": stages,
        "summary": summary,
    }
    _save_json(workflow_state_path_for(repo_dir), state)
    return state


def apply_workflow_gate_to_validation_report(
    *,
    repo_dir: Path,
    validation_report: dict[str, Any],
    workflow_state: dict[str, Any],
) -> dict[str, Any]:
    """Merge workflow readiness into the persisted validation report."""

    report = dict(validation_report)
    validation_ready = bool(validation_report.get("final_ready"))
    workflow_summary = workflow_state.get("summary")
    if not isinstance(workflow_summary, dict):
        workflow_summary = {}
    workflow_ready = bool(workflow_summary.get("workflow_ready"))
    quality_ready = bool(workflow_summary.get("quality_ready"))
    completion_ready = validation_ready and workflow_ready and quality_ready
    report["validation_ready"] = validation_ready
    report["workflow_ready"] = workflow_ready
    report["quality_ready"] = quality_ready
    report["final_ready"] = completion_ready
    report["workflow"] = {
        "path": f"{DRVLOOP_STATE_DIRNAME}/{DRVLOOP_WORKFLOW_FILENAME}",
        "proof_depth": workflow_state.get("proof_depth"),
        "summary": workflow_summary,
    }
    summary = report.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    summary = dict(summary)
    summary["validation_ready"] = validation_ready
    summary["workflow_ready"] = workflow_ready
    summary["quality_ready"] = quality_ready
    summary["completion_ready"] = completion_ready
    summary["final_ready"] = completion_ready
    report["summary"] = summary
    _save_json(
        repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_VALIDATION_REPORT_FILENAME,
        report,
    )
    return report


def format_workflow_feedback(state: dict[str, Any]) -> str:
    summary = state.get("summary") if isinstance(state.get("summary"), dict) else {}
    metrics = state.get("metrics") if isinstance(state.get("metrics"), dict) else {}
    lines = [
        f"- proof_depth: {state.get('proof_depth')}",
        f"- current_iteration: {state.get('current_iteration')}",
        f"- workflow_ready: {bool(summary.get('workflow_ready'))}",
        f"- quality_ready: {bool(summary.get('quality_ready'))}",
        f"- completion_ready: {bool(summary.get('completion_ready'))}",
        (
            "- route progress: "
            f"{metrics.get('route_candidate_count', 0)} candidate(s), "
            f"{metrics.get('developed_route_count', 0)} developed route(s)"
        ),
        (
            "- manuscript depth: "
            f"{metrics.get('best_manuscript_lines', 0)} line(s), "
            f"{metrics.get('best_manuscript_equations', 0)} equation block(s)"
        ),
        (
            "- obligations: "
            f"{metrics.get('explicit_obligation_count', 0)} explicit, "
            f"{metrics.get('strong_target_obligation_count', 0)} strong target-covering"
        ),
    ]
    next_stage = summary.get("next_stage")
    if next_stage:
        lines.append(f"- next_stage: {next_stage}")
    blockers = summary.get("blockers")
    if isinstance(blockers, list) and blockers:
        for blocker in blockers[:12]:
            lines.append(f"- blocker: {blocker}")
    guidance = summary.get("guidance")
    if isinstance(guidance, list) and guidance:
        for item in guidance[:8]:
            lines.append(f"- guidance: {item}")
    return "\n".join(lines)


def workflow_completion_ready(state: dict[str, Any]) -> bool:
    summary = state.get("summary")
    return isinstance(summary, dict) and bool(summary.get("completion_ready"))


def _scan_project_artifacts(repo_dir: Path, project_path: Path) -> list[dict[str, Any]]:
    if not project_path.is_dir():
        return []
    artifacts: list[dict[str, Any]] = []
    for path in sorted(project_path.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            continue
        suffix = path.suffix.lower()
        text = ""
        if suffix in _TEXT_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
        line_count = len(text.splitlines()) if text else 0
        artifacts.append(
            {
                "path": rel,
                "name": path.name,
                "stem": path.stem,
                "suffix": suffix,
                "line_count": line_count,
                "equation_blocks": len(_DISPLAY_MATH_RE.findall(text)) if text else 0,
                "route_mentions": _count_route_mentions(text) if text else 0,
                "contains_ranking": _contains_any(
                    text,
                    ("rank", "score", "selected route", "winner", "tradeoff"),
                ),
                "contains_review": _contains_any(
                    text,
                    ("gap", "review", "limitation", "unchecked", "remaining"),
                ),
            }
        )
    return artifacts


def _build_metrics(
    *,
    repo_dir: Path,
    artifacts: list[dict[str, Any]],
    validation_report: dict[str, Any],
    profile: WorkflowProfile,
) -> dict[str, Any]:
    route_population = [
        item for item in artifacts if _is_route_population_artifact(item)
    ]
    developed_routes = [
        item for item in artifacts if _is_developed_route_artifact(item, profile)
    ]
    route_ids = _route_ids_from_validation(validation_report)
    route_candidate_count = max(
        len(route_ids),
        len(developed_routes),
        max(
            (int(item.get("route_mentions") or 0) for item in route_population),
            default=0,
        ),
    )
    manuscript_artifacts = [item for item in artifacts if _is_manuscript_artifact(item)]
    note_artifacts = [item for item in artifacts if _is_note_artifact(item)]
    synthesis_artifacts = [item for item in artifacts if _is_synthesis_artifact(item)]
    gap_review_artifacts = [item for item in artifacts if _is_gap_review_artifact(item)]
    consistency_review_artifacts = [
        item for item in artifacts if _is_final_consistency_artifact(item)
    ]
    route_ranking_artifacts = [
        item
        for item in artifacts
        if str(item.get("suffix") or "").lower() in _TEXT_SUFFIXES
        and (
            "rank" in str(item.get("stem", "")).lower()
            or "ranking" in str(item.get("stem", "")).lower()
            or bool(item.get("contains_ranking"))
        )
    ]
    numeric_artifacts = [item for item in artifacts if _is_numeric_artifact(item)]
    obligations = validation_report.get("obligations")
    if not isinstance(obligations, list):
        obligations = []
    explicit_obligations = [
        item
        for item in obligations
        if isinstance(item, dict) and not item.get("auto_extracted")
    ]
    strong_target_obligations = [
        item
        for item in explicit_obligations
        if item.get("status") == "passed"
        and item.get("covers_target_claims")
        and _is_strong_target_type(item)
    ]
    best_manuscript = _best_artifact(manuscript_artifacts)
    best_note = _best_artifact(note_artifacts)
    return {
        "route_candidate_count": route_candidate_count,
        "developed_route_count": len(developed_routes),
        "route_ids": sorted(route_ids),
        "route_population_artifacts": [item["path"] for item in route_population],
        "developed_route_artifacts": [item["path"] for item in developed_routes],
        "route_ranking_artifacts": [item["path"] for item in route_ranking_artifacts],
        "synthesis_artifacts": [item["path"] for item in synthesis_artifacts],
        "gap_review_artifacts": [item["path"] for item in gap_review_artifacts],
        "final_consistency_review_artifacts": [
            item["path"] for item in consistency_review_artifacts
        ],
        "numeric_artifacts": [item["path"] for item in numeric_artifacts],
        "manuscript_artifacts": [item["path"] for item in manuscript_artifacts],
        "pedagogical_note_artifacts": [item["path"] for item in note_artifacts],
        "best_manuscript_lines": int(best_manuscript.get("line_count") or 0),
        "best_manuscript_equations": int(best_manuscript.get("equation_blocks") or 0),
        "best_pedagogical_note_lines": int(best_note.get("line_count") or 0),
        "explicit_obligation_count": len(explicit_obligations),
        "strong_target_obligation_count": len(strong_target_obligations),
        "validation_ready": bool(validation_report.get("final_ready")),
        "workflow_state_path": (
            repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_WORKFLOW_FILENAME
        )
        .relative_to(repo_dir)
        .as_posix(),
    }


def _build_stages(
    metrics: dict[str, Any],
    validation_report: dict[str, Any],
    profile: WorkflowProfile,
    iteration: int,
) -> dict[str, dict[str, Any]]:
    stages: dict[str, dict[str, Any]] = {}
    stages["turn_budget"] = _stage(
        iteration >= profile.min_iterations,
        f"{iteration}/{profile.min_iterations} required iteration(s) observed",
    )
    stages["route_population"] = _stage(
        int(metrics["route_candidate_count"]) >= profile.min_route_candidates,
        (
            f"{metrics['route_candidate_count']}/{profile.min_route_candidates} "
            "candidate route(s) recorded"
        ),
    )
    for index in range(1, profile.required_pathways + 1):
        stages[f"pathway_{index}"] = _stage(
            int(metrics["developed_route_count"]) >= index,
            (
                f"{metrics['developed_route_count']}/{index} developed pathway "
                "artifact(s) available"
            ),
        )
    stages["route_ranking"] = _stage(
        (not profile.require_route_ranking) or bool(metrics["route_ranking_artifacts"]),
        "route ranking/comparison artifact present",
    )
    stages["synthesis"] = _stage(
        (not profile.require_synthesis) or bool(metrics["synthesis_artifacts"]),
        "official synthesis artifact present",
    )
    stages["manuscript_draft"] = _stage(
        int(metrics["best_manuscript_lines"]) >= profile.min_manuscript_lines
        and int(metrics["best_manuscript_equations"])
        >= profile.min_manuscript_equations,
        (
            f"{metrics['best_manuscript_lines']}/{profile.min_manuscript_lines} "
            "manuscript line(s), "
            f"{metrics['best_manuscript_equations']}/{profile.min_manuscript_equations} "
            "equation block(s)"
        ),
    )
    stages["gap_review"] = _stage(
        (not profile.require_gap_review) or bool(metrics["gap_review_artifacts"]),
        "manuscript gap-review artifact present",
    )
    stages["numerical_checks"] = _stage(
        (not profile.require_numerical_checks) or bool(metrics["numeric_artifacts"]),
        "numerical check script/summary artifact present",
    )
    stages["pedagogical_note"] = _stage(
        int(metrics["best_pedagogical_note_lines"]) >= profile.min_note_lines,
        (
            f"{metrics['best_pedagogical_note_lines']}/{profile.min_note_lines} "
            "pedagogical-note line(s)"
        ),
    )
    stages["proof_obligation_depth"] = _stage(
        int(metrics["explicit_obligation_count"]) >= profile.min_explicit_obligations
        and int(metrics["strong_target_obligation_count"])
        >= profile.min_strong_target_obligations,
        (
            f"{metrics['explicit_obligation_count']}/{profile.min_explicit_obligations} "
            "explicit obligation(s), "
            f"{metrics['strong_target_obligation_count']}/"
            f"{profile.min_strong_target_obligations} strong target-covering "
            "obligation(s)"
        ),
    )
    stages["final_consistency_review"] = _stage(
        (not profile.require_final_consistency_review)
        or bool(metrics["final_consistency_review_artifacts"]),
        "final consistency-review artifact present",
    )
    stages["final_packaging"] = _stage(
        bool(validation_report.get("final_ready"))
        and bool(metrics["manuscript_artifacts"])
        and bool(metrics["pedagogical_note_artifacts"]),
        "validator-ready final manuscript and pedagogical note present",
    )
    return stages


def _summarize_workflow(
    stages: dict[str, dict[str, Any]],
    metrics: dict[str, Any],
    validation_report: dict[str, Any],
    profile: WorkflowProfile,
) -> dict[str, Any]:
    open_stages = [
        name for name, stage in stages.items() if stage.get("status") != "complete"
    ]
    actionable_open_stages = [name for name in open_stages if name != "turn_budget"]
    next_stage = (
        actionable_open_stages[0]
        if actionable_open_stages
        else (open_stages[0] if open_stages else None)
    )
    blockers = [f"{name}: {stages[name].get('detail')}" for name in open_stages]
    validation_ready = bool(validation_report.get("final_ready"))
    workflow_ready = not open_stages
    quality_blockers: list[str] = []
    if int(metrics["explicit_obligation_count"]) < profile.min_explicit_obligations:
        quality_blockers.append("too few explicit proof obligations")
    if (
        int(metrics["strong_target_obligation_count"])
        < profile.min_strong_target_obligations
    ):
        quality_blockers.append("target coverage relies on too few strong obligations")
    if int(metrics["route_candidate_count"]) < profile.min_route_candidates:
        quality_blockers.append("route population is too small")
    if int(metrics["developed_route_count"]) < profile.required_pathways:
        quality_blockers.append("not enough developed independent pathways")
    quality_ready = not quality_blockers and workflow_ready
    guidance = _guidance_for_next_stage(str(next_stage or ""), profile)
    return {
        "validation_ready": validation_ready,
        "workflow_ready": workflow_ready,
        "quality_ready": quality_ready,
        "completion_ready": validation_ready and workflow_ready and quality_ready,
        "next_stage": next_stage,
        "open_stages": open_stages,
        "blockers": blockers + quality_blockers,
        "guidance": guidance,
    }


def _guidance_for_next_stage(stage: str, profile: WorkflowProfile) -> list[str]:
    if stage == "turn_budget":
        return [
            "Continue with the next substantive derivation stage; do not finalize early."
        ]
    if stage == "route_population":
        return [
            (
                f"Write a route-population artifact with at least "
                f"{profile.min_route_candidates} independent derivation pathways, "
                "including assumptions, risks, and expected validation kernels."
            )
        ]
    if stage.startswith("pathway_"):
        return [
            "Develop one pathway in a dedicated artifact with equations, assumptions, failure modes, and proof obligations."
        ]
    if stage == "route_ranking":
        return [
            "Rank or compare the populated routes using target coverage and unresolved obligations."
        ]
    if stage == "synthesis":
        return [
            "Write an official synthesis artifact that chooses and combines the strongest routes."
        ]
    if stage == "manuscript_draft":
        return [
            "Expand the final manuscript with self-contained derivations and equation-level support."
        ]
    if stage == "gap_review":
        return [
            "Write a gap review that looks for hidden lemmas, unsupported transitions, and assumption drift."
        ]
    if stage == "numerical_checks":
        return [
            "Add trusted numerical spot checks or scripts for the main analytical claims."
        ]
    if stage == "pedagogical_note":
        return [
            "Write a substantial pedagogical note with intermediate derivation details."
        ]
    if stage == "proof_obligation_depth":
        return [
            "Add granular algebra, limit, commutator, dimensional, and numerical obligations for target claims."
        ]
    if stage == "final_consistency_review":
        return [
            "Write a final consistency review after manuscript, numerics, and obligations are complete."
        ]
    return []


def _stage(done: bool, detail: str) -> dict[str, Any]:
    return {"status": "complete" if done else "open", "detail": detail}


def _observed_drvloop_round_count(repo_dir: Path) -> int:
    stream_count = 0
    stream_dir = repo_dir / "projects" / "agent_streams"
    if stream_dir.is_dir():
        stream_count = sum(1 for path in stream_dir.glob("*.jsonl") if path.is_file())
    memory_round = 0
    memory_path = repo_dir / "projects" / "memory.md"
    try:
        memory_text = memory_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        memory_text = ""
    for match in _MEMORY_ROUND_RE.finditer(memory_text):
        try:
            memory_round = max(memory_round, int(match.group(1)))
        except ValueError:
            continue
    return max(stream_count, memory_round)


def _is_route_population_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    return (
        "pathways" in stem
        or "route_plan" in stem
        or stem in {"routes", "route_population", "sketch_population"}
    )


def _is_developed_route_artifact(
    item: dict[str, Any], profile: WorkflowProfile
) -> bool:
    stem = str(item.get("stem") or "").lower()
    if "route_plan" in stem or "pathways" == stem or stem.endswith("pathways"):
        return False
    route_like = bool(
        re.search(r"(?:^|[_-])(?:\d+_)?pathway\d*(?:$|[_-])", stem)
        or re.search(r"(?:^|[_-])route[-_][a-z0-9]+", stem)
    )
    return (
        route_like
        and int(item.get("line_count") or 0) >= profile.min_developed_route_lines
    )


def _is_manuscript_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    if suffix not in _TEXT_SUFFIXES:
        return False
    if "review" in stem or "note" in stem or "pedagogical" in stem:
        return False
    return any(
        token in stem
        for token in ("manuscript", "preprint", "final_derivation", "final_manuscript")
    )


def _is_note_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    return suffix in _TEXT_SUFFIXES and any(
        token in stem for token in ("pedagogical", "supplement", "appendix")
    )


def _is_synthesis_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    return suffix in _TEXT_SUFFIXES and any(
        token in stem for token in ("synthesis", "official_derivation")
    )


def _is_gap_review_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    return (
        suffix in _TEXT_SUFFIXES
        and "review" in stem
        and ("gap" in stem or bool(item.get("contains_review")))
    )


def _is_final_consistency_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    return (
        suffix in _TEXT_SUFFIXES
        and "review" in stem
        and ("consistency" in stem or "final" in stem)
    )


def _is_numeric_artifact(item: dict[str, Any]) -> bool:
    stem = str(item.get("stem") or "").lower()
    suffix = str(item.get("suffix") or "").lower()
    return (
        suffix in _SCRIPT_SUFFIXES
        and any(
            token in stem for token in ("numeric", "numerical", "check", "validation")
        )
    ) or (
        suffix == ".json"
        and any(
            token in stem for token in ("numeric", "numerical", "summary", "validation")
        )
    )


def _best_artifact(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return {}
    return max(
        items,
        key=lambda item: (
            int(item.get("line_count") or 0),
            int(item.get("equation_blocks") or 0),
        ),
    )


def _route_ids_from_validation(validation_report: dict[str, Any]) -> set[str]:
    obligations = validation_report.get("obligations")
    if not isinstance(obligations, list):
        return set()
    ids: set[str] = set()
    for item in obligations:
        if not isinstance(item, dict):
            continue
        route_id = str(item.get("route_id") or "").strip()
        if route_id and route_id not in {"final", "default"}:
            ids.add(route_id)
    return ids


def _is_strong_target_type(item: dict[str, Any]) -> bool:
    kind = str(item.get("type") or "").strip().lower().replace("-", "_")
    return kind not in _WEAK_TARGET_TYPES


def _count_route_mentions(text: str) -> int:
    labels = set()
    for line in text.splitlines():
        match = _ROUTE_LINE_RE.search(line)
        if match:
            labels.add(match.group(1).lower())
    return len(labels)


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
