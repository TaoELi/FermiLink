from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fermilink.drvloop.prompts import DRVLOOP_SKETCHES_FILENAME, DRVLOOP_STATE_DIRNAME
from fermilink.drvloop.spec import DerivationSpecContext


def sketches_path_for(repo_dir: Path) -> Path:
    return repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_SKETCHES_FILENAME


def update_sketch_population(
    *,
    repo_dir: Path,
    spec_context: DerivationSpecContext,
    validation_report: dict[str, Any],
    artifact_changes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append a route/sketch population record and return latest records."""

    latest = _latest_records(repo_dir)
    route_ids = _route_ids(validation_report, artifact_changes, spec_context)
    path = sketches_path_for(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for route_id in route_ids:
            previous = latest.get(route_id, {})
            visits = int(previous.get("visits") or 0) + 1
            record = _build_record(
                route_id=route_id,
                spec_context=spec_context,
                validation_report=validation_report,
                artifact_changes=artifact_changes,
                visits=visits,
            )
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            latest[route_id] = record
    return sorted(latest.values(), key=_sort_key, reverse=True)


def format_sketch_population(records: list[dict[str, Any]]) -> str:
    if not records:
        return "- No sketch population records yet."
    lines: list[str] = []
    for item in records[:8]:
        lines.append(
            f"- {item.get('route_id')}: score={item.get('score')} "
            f"visits={item.get('visits')} final_ready={item.get('final_ready')} "
            f"passed={item.get('passed')} failed={item.get('failed')} "
            f"critical_unresolved={item.get('critical_unresolved')} "
            f"targets_open={item.get('target_claims_open')}"
        )
        summary = str(item.get("summary") or "").strip()
        if summary:
            lines.append(f"  summary: {summary}")
    return "\n".join(lines)


def _build_record(
    *,
    route_id: str,
    spec_context: DerivationSpecContext,
    validation_report: dict[str, Any],
    artifact_changes: list[dict[str, Any]],
    visits: int,
) -> dict[str, Any]:
    summary = validation_report.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    obligations = validation_report.get("obligations")
    if not isinstance(obligations, list):
        obligations = []
    route_obligations = [
        item
        for item in obligations
        if isinstance(item, dict) and str(item.get("route_id") or "default") == route_id
    ]
    if route_obligations:
        obligations = route_obligations
    changed_paths = [
        str(item.get("path") or "")
        for item in artifact_changes
        if str(item.get("path") or "").strip()
        and _route_id_for_path(str(item.get("path") or ""), spec_context) == route_id
    ]
    open_obligations = [
        str(item.get("id") or "")
        for item in obligations
        if isinstance(item, dict) and item.get("status") in {"open", "unknown"}
    ]
    failed_obligations = [
        str(item.get("id") or "")
        for item in obligations
        if isinstance(item, dict) and item.get("status") == "failed"
    ]
    target_claims_covered = sorted(
        {
            str(claim)
            for item in obligations
            if isinstance(item, dict) and item.get("status") == "passed"
            for claim in item.get("covers_target_claims") or []
        }
    )
    source_artifacts = sorted(
        {
            str(item.get("source_file") or "")
            for item in obligations
            if isinstance(item, dict) and str(item.get("source_file") or "").strip()
        }
    )
    novelty_notes = [
        str(item.get("novelty_note") or item.get("novelty_notes") or "")
        for item in obligations
        if isinstance(item, dict)
        and str(item.get("novelty_note") or item.get("novelty_notes") or "").strip()
    ]
    score = _score(summary)
    return {
        "schema_version": 1,
        "recorded_at_utc": _utc_now_z(),
        "route_id": route_id,
        "project": spec_context.project_rel,
        "spec": spec_context.spec_rel,
        "visits": visits,
        "score": score,
        "final_ready": bool(validation_report.get("final_ready")),
        "passed": _count_status(obligations, "passed"),
        "failed": _count_status(obligations, "failed"),
        "open": _count_status(obligations, "open"),
        "unknown": _count_status(obligations, "unknown"),
        "validator_pass_count": _count_status(obligations, "passed"),
        "critical_unresolved": _count_critical_unresolved(obligations),
        "target_claims_open": int(summary.get("target_claims_open") or 0),
        "target_claims_covered": target_claims_covered,
        "source_artifacts": [item for item in source_artifacts if item],
        "open_obligations": [item for item in open_obligations if item],
        "failed_obligations": [item for item in failed_obligations if item],
        "novelty_notes": [item for item in novelty_notes if item],
        "recent_artifacts": changed_paths[:20],
        "summary": _summary_text(summary),
    }


def _score(summary: dict[str, Any]) -> float:
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    critical_failed = int(summary.get("critical_failed") or 0)
    critical_unresolved = int(summary.get("critical_unresolved") or 0)
    targets_open = int(summary.get("target_claims_open") or 0)
    unknown = int(summary.get("unknown") or 0)
    final_bonus = 20 if bool(summary.get("final_ready")) else 0
    return float(
        final_bonus
        + passed * 3
        - failed * 5
        - critical_failed * 8
        - critical_unresolved * 4
        - targets_open * 6
        - unknown
    )


def _route_ids(
    validation_report: dict[str, Any],
    artifact_changes: list[dict[str, Any]],
    spec_context: DerivationSpecContext,
) -> list[str]:
    ids: set[str] = set()
    obligations = validation_report.get("obligations")
    if isinstance(obligations, list):
        for item in obligations:
            if not isinstance(item, dict):
                continue
            route_id = str(item.get("route_id") or "").strip()
            if route_id:
                ids.add(route_id)
    for item in artifact_changes:
        path = str(item.get("path") or "").strip()
        if path:
            ids.add(_route_id_for_path(path, spec_context))
    if not ids:
        ids.add(Path(spec_context.project_rel).name)
    return sorted(ids)


def _route_id_for_path(path: str, spec_context: DerivationSpecContext) -> str:
    rel = Path(path)
    project = Path(spec_context.project_rel)
    try:
        local = rel.relative_to(project)
    except ValueError:
        if len(rel.parts) >= 3 and rel.parts[0] == "projects":
            return Path(rel.name).stem or rel.parts[1]
        return Path(spec_context.project_rel).name
    if len(local.parts) > 1:
        return local.parts[0]
    stem = Path(local.name).stem
    if stem in {"proof_obligations", "derivation_spec", "memory"}:
        return Path(spec_context.project_rel).name
    return stem or Path(spec_context.project_rel).name


def _count_status(obligations: list[Any], status: str) -> int:
    return sum(
        1
        for item in obligations
        if isinstance(item, dict) and item.get("status") == status
    )


def _count_critical_unresolved(obligations: list[Any]) -> int:
    return sum(
        1
        for item in obligations
        if isinstance(item, dict)
        and bool(item.get("critical"))
        and item.get("status") in {"open", "unknown"}
    )


def _summary_text(summary: dict[str, Any]) -> str:
    if bool(summary.get("final_ready")):
        return "validator reports final-ready derivation"
    blockers: list[str] = []
    if int(summary.get("target_claims_open") or 0):
        blockers.append(
            f"{summary.get('target_claims_open')} target claim(s) uncovered"
        )
    if int(summary.get("critical_failed") or 0):
        blockers.append(f"{summary.get('critical_failed')} critical failure(s)")
    if int(summary.get("critical_unresolved") or 0):
        blockers.append(f"{summary.get('critical_unresolved')} critical unresolved")
    return ", ".join(blockers) if blockers else "partial sketch recorded"


def _latest_records(repo_dir: Path) -> dict[str, dict[str, Any]]:
    path = sketches_path_for(repo_dir)
    if not path.is_file():
        return {}
    latest: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        route_id = str(payload.get("route_id") or "").strip()
        if route_id:
            latest[route_id] = payload
    return latest


def _sort_key(item: dict[str, Any]) -> tuple[float, int]:
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        visits = int(item.get("visits") or 0)
    except (TypeError, ValueError):
        visits = 0
    return score, -visits


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
