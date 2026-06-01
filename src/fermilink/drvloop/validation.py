from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from fermilink.drvloop import backends
from fermilink.drvloop.prompts import (
    DRVLOOP_GOAL_CACHE_FILENAME,
    DRVLOOP_OBLIGATIONS_FILENAME,
    DRVLOOP_STATE_DIRNAME,
    DRVLOOP_VALIDATION_REPORT_FILENAME,
)
from fermilink.drvloop.spec import DerivationSpecContext, target_claim_ids


_OBLIGATION_FENCE_RE = re.compile(
    r"```(?:yaml\s+)?(?:drvloop-obligations|proof-obligations)\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_DISPLAY_MATH_RES = (
    re.compile(r"\$\$(.*?)\$\$", re.DOTALL),
    re.compile(r"\\\[(.*?)\\\]", re.DOTALL),
    re.compile(r"\\begin\{equation\*?\}(.*?)\\end\{equation\*?\}", re.DOTALL),
    re.compile(r"\\begin\{align\*?\}(.*?)\\end\{align\*?\}", re.DOTALL),
)
_TRANSITION_RE = re.compile(
    r"\b(therefore|hence|thus|it follows|we obtain|we get|implies|consequently)\b",
    re.IGNORECASE,
)
_ASSUMPTION_RE = re.compile(
    r"\b(assume|assuming|approximation|neglect|adiabatic|markov|rotating[- ]wave)\b",
    re.IGNORECASE,
)
_HIDDEN_LEMMA_RE = re.compile(
    r"\b(standard|well-known|obvious|straightforward|can be shown|it is known|clearly)\b",
    re.IGNORECASE,
)
_CITATION_PLACEHOLDER_RE = re.compile(
    r"(citation needed|todo\s*:?\s*cite|tbd|\\cite\{\s*\}|ref\??)",
    re.IGNORECASE,
)
_SUPPORTED_TEXT_SUFFIXES = {".md", ".tex", ".rst"}
_AUTO_EXTRACT_SUFFIXES = {".md", ".rst"}
_STRONG_TARGET_COVERAGE_TYPES = {
    "algebra",
    "identity",
    "commutator",
    "operator_identity",
    "limit",
    "asymptotic",
    "dimension",
    "unit",
    "units",
    "unit_consistency",
    "numeric_check",
    "numerical_check",
    "numerical",
    "trace_preservation",
    "trace",
    "hermiticity",
    "hermitian",
    "tensor_index",
    "tensor_indices",
    "index_contraction",
    "bch_order",
    "perturbation_order",
    "order_bookkeeping",
    "conservation",
    "conservation_law",
    "formal_optional",
    "lean",
    "formal",
}


def validation_report_path_for(repo_dir: Path) -> Path:
    return repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_VALIDATION_REPORT_FILENAME


def goal_cache_path_for(repo_dir: Path) -> Path:
    return repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_GOAL_CACHE_FILENAME


def run_drvloop_validation(
    *,
    repo_dir: Path,
    spec_context: DerivationSpecContext,
) -> dict[str, Any]:
    """Validate derivation proof obligations and persist a report."""

    obligations = collect_obligations(repo_dir)
    obligations.extend(_final_artifact_obligations(repo_dir, spec_context))
    cache = _load_goal_cache(repo_dir)
    validated: list[dict[str, Any]] = []
    spec_assumptions = _string_list(spec_context.payload.get("assumptions"))
    for obligation in obligations:
        key = _obligation_cache_key(obligation)
        cached = cache.get(key)
        if (
            isinstance(cached, dict)
            and cached.get("status") == "passed"
            and _can_use_cached_result(obligation)
        ):
            result = {
                "status": cached.get("status"),
                "reason": cached.get("reason", "cached"),
                "evidence": cached.get("evidence", {}),
                "cached": True,
            }
        else:
            result = validate_obligation(
                repo_dir=repo_dir,
                obligation=obligation,
                spec_assumptions=spec_assumptions,
            )
        record = {
            **obligation,
            **result,
            "validator": result.get("validator") or _validator_name(obligation),
            "cache_key": key,
        }
        validated.append(record)
        if record.get("status") in {"passed", "failed"}:
            cache[key] = {
                "status": record.get("status"),
                "reason": record.get("reason"),
                "evidence": record.get("evidence", {}),
                "updated_at_utc": _utc_now_z(),
            }

    target_status = _target_claim_status(spec_context.payload, validated)
    review_findings = _review_findings(
        spec_context=spec_context,
        obligations=validated,
        target_status=target_status,
    )
    summary = _summarize(
        validated,
        target_status,
        spec_context.integrity,
        review_findings=review_findings,
    )
    report = {
        "schema_version": 1,
        "generated_at_utc": _utc_now_z(),
        "spec": {
            "project": spec_context.project_rel,
            "path": spec_context.spec_rel,
            "integrity": spec_context.integrity,
        },
        "summary": summary,
        "target_claims": target_status,
        "review_findings": review_findings,
        "obligations": validated,
        "final_ready": bool(summary.get("final_ready")),
    }
    _save_json(validation_report_path_for(repo_dir), report)
    _save_json(goal_cache_path_for(repo_dir), {"schema_version": 1, "goals": cache})
    return report


def collect_obligations(repo_dir: Path) -> list[dict[str, Any]]:
    projects_dir = repo_dir / "projects"
    if not projects_dir.is_dir():
        return []
    explicit: list[dict[str, Any]] = []
    text_artifacts: list[tuple[Path, str]] = []
    for path in sorted(projects_dir.rglob("*")):
        if not path.is_file() or path.name == "memory.md":
            continue
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            continue
        if path.name in {DRVLOOP_OBLIGATIONS_FILENAME, "proof_obligations.yml"}:
            explicit.extend(_load_obligations_from_yaml(path, rel))
            continue
        if path.suffix.lower() in _SUPPORTED_TEXT_SUFFIXES:
            fenced = _load_obligations_from_fences(path, rel)
            explicit.extend(fenced)
            if path.suffix.lower() in _AUTO_EXTRACT_SUFFIXES:
                text_artifacts.append((path, rel))
    explicit_source_files = {
        str(item.get("source_file") or "").strip()
        for item in explicit
        if str(item.get("source_file") or "").strip()
    }
    auto: list[dict[str, Any]] = []
    for path, rel in text_artifacts:
        extracted = _extract_auto_obligations(path, rel)
        if rel in explicit_source_files:
            for item in extracted:
                if item.get("type") == "manual":
                    item["critical"] = False
                    item["reason"] = "explicit_obligations_exist_for_source_file"
        auto.extend(extracted)
    return _dedupe_obligations([*explicit, *auto])


def validate_obligation(
    *,
    repo_dir: Path,
    obligation: dict[str, Any],
    spec_assumptions: list[str],
) -> dict[str, Any]:
    kind = str(obligation.get("type") or "").strip().lower().replace("-", "_")
    if kind in {"algebra", "identity"}:
        return backends.validate_algebra(obligation)
    if kind in {"commutator", "operator_identity"}:
        return backends.validate_commutator(obligation)
    if kind in {"limit", "asymptotic"}:
        return backends.validate_limit(obligation)
    if kind in {"trace_preservation", "trace"}:
        return backends.validate_trace_preservation(obligation)
    if kind in {"hermiticity", "hermitian"}:
        return backends.validate_hermiticity(obligation)
    if kind in {"tensor_index", "tensor_indices", "index_contraction"}:
        return backends.validate_tensor_index(obligation)
    if kind in {"bch_order", "perturbation_order", "order_bookkeeping"}:
        return backends.validate_perturbation_order(obligation)
    if kind in {"conservation", "conservation_law"}:
        return backends.validate_algebra(obligation)
    if kind in {"dimension", "unit", "units", "unit_consistency"}:
        return backends.validate_dimension(obligation)
    if kind in {"numeric_check", "numerical_check", "numerical"}:
        return backends.validate_numeric(repo_dir, obligation)
    if kind in {"latex_build", "latex"}:
        return backends.validate_latex(repo_dir, obligation)
    if kind in {"formal_optional", "lean", "formal"}:
        return backends.validate_formal_optional(repo_dir, obligation)
    if kind in {"final_artifact", "final_manuscript", "pedagogical_note"}:
        return backends.validate_final_artifact(repo_dir, obligation)
    if kind in {"citation", "literature"}:
        return backends.validate_citation(obligation)
    if kind in {"assumption", "approximation"}:
        return backends.validate_assumption(
            obligation,
            spec_assumptions=spec_assumptions,
        )
    if kind in {"manual", "review"}:
        return {
            "status": "unknown",
            "reason": "manual_review_required",
            "evidence": {},
        }
    return {"status": "open", "reason": "unsupported_obligation_type", "evidence": {}}


def format_validation_feedback(report: dict[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines = [
        f"- final_ready: {bool(report.get('final_ready'))}",
        f"- validation_ready: {bool(report.get('validation_ready', report.get('final_ready')))}",
        f"- workflow_ready: {bool(report.get('workflow_ready', True))}",
        f"- quality_ready: {bool(report.get('quality_ready', True))}",
        (
            "- obligations: "
            f"{summary.get('passed', 0)} passed, "
            f"{summary.get('failed', 0)} failed, "
            f"{summary.get('open', 0)} open, "
            f"{summary.get('unknown', 0)} unknown"
        ),
        f"- critical_unresolved: {summary.get('critical_unresolved', 0)}",
        f"- critical_failed: {summary.get('critical_failed', 0)}",
        f"- target_claims_open: {summary.get('target_claims_open', 0)}",
        f"- review_critical: {summary.get('review_critical', 0)}",
    ]
    target_claims = report.get("target_claims")
    if isinstance(target_claims, list):
        for item in target_claims[:10]:
            if not isinstance(item, dict):
                continue
            covered_by = item.get("covered_by")
            if not isinstance(covered_by, list):
                covered_by = []
            lines.append(
                f"- target {item.get('id')}: {item.get('status')} "
                f"(covered_by={', '.join(str(x) for x in covered_by) or 'none'})"
            )
    obligations = report.get("obligations")
    if isinstance(obligations, list):
        blockers = [
            item
            for item in obligations
            if isinstance(item, dict)
            and item.get("critical")
            and item.get("status") in {"failed", "open", "unknown"}
        ]
        for item in blockers[:10]:
            lines.append(
                "- blocker "
                f"{item.get('id')}: {item.get('status')} "
                f"{item.get('reason')} | {item.get('claim', '')}"
            )
    review_findings = report.get("review_findings")
    if isinstance(review_findings, list):
        for item in review_findings[:10]:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- reviewer "
                f"{item.get('severity')}: {item.get('id')} | {item.get('message')}"
            )
    return "\n".join(lines)


def _load_obligations_from_yaml(path: Path, rel: str) -> list[dict[str, Any]]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return []
    return _normalize_obligation_payload(payload, rel)


def _load_obligations_from_fences(path: Path, rel: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    obligations: list[dict[str, Any]] = []
    for match in _OBLIGATION_FENCE_RE.finditer(text):
        try:
            payload = yaml.safe_load(match.group(1))
        except yaml.YAMLError:
            continue
        obligations.extend(_normalize_obligation_payload(payload, rel))
    return obligations


def _extract_auto_obligations(path: Path, rel: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    obligations: list[dict[str, Any]] = []
    for equation in _extract_display_equations(text)[:20]:
        if "=" not in equation:
            continue
        obligations.append(
            _auto_obligation(
                rel,
                kind="equation",
                claim=(
                    "Auto-extracted equation requires an explicit validating "
                    f"obligation: {_shorten(equation)}"
                ),
            )
        )
    for line in _interesting_lines(text, _TRANSITION_RE)[:20]:
        obligations.append(
            _auto_obligation(
                rel,
                kind="transition",
                claim=(
                    "Auto-extracted derivation transition requires justification: "
                    f"{_shorten(line)}"
                ),
            )
        )
    for line in _interesting_lines(text, _ASSUMPTION_RE)[:20]:
        obligations.append(
            _auto_obligation(
                rel,
                kind="assumption",
                claim=f"Auto-extracted assumption or approximation: {_shorten(line)}",
                obligation_type="assumption",
            )
        )
    for line in _interesting_lines(text, _CITATION_PLACEHOLDER_RE)[:20]:
        obligations.append(
            _auto_obligation(
                rel,
                kind="citation",
                claim=f"Auto-extracted citation placeholder: {_shorten(line)}",
                obligation_type="citation",
            )
        )
    for line in _interesting_lines(text, _HIDDEN_LEMMA_RE)[:20]:
        if "\\cite" in line or "[@" in line:
            continue
        obligations.append(
            _auto_obligation(
                rel,
                kind="hidden-lemma",
                claim=f"Auto-extracted unsupported lemma-style claim: {_shorten(line)}",
            )
        )
    return _dedupe_obligations(obligations[:80])


def _normalize_obligation_payload(
    payload: Any, source_rel: str
) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        raw_items = payload.get("obligations")
        if raw_items is None and "id" in payload:
            raw_items = [payload]
    elif isinstance(payload, list):
        raw_items = payload
    else:
        raw_items = []
    if not isinstance(raw_items, list):
        return []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        record = dict(item)
        record.setdefault("id", f"{Path(source_rel).stem}-{index}")
        record.setdefault("source_file", source_rel)
        record["type"] = str(record.get("type") or "manual").strip().lower()
        record.setdefault("route_id", _infer_route_id(source_rel))
        covers = _normalize_covers(record.get("covers_target_claims"))
        if covers:
            record["covers_target_claims"] = covers
        record["critical"] = _default_critical(record)
        normalized.append(record)
    return normalized


def _dedupe_obligations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("id") or _obligation_cache_key(item))
        source = str(item.get("source_file") or "")
        combined = f"{source}:{key}"
        if combined in seen:
            continue
        seen.add(combined)
        deduped.append(item)
    return deduped


def _final_artifact_obligations(
    repo_dir: Path,
    spec_context: DerivationSpecContext,
) -> list[dict[str, Any]]:
    final_config = spec_context.payload.get("final_artifacts")
    if not isinstance(final_config, dict):
        final_config = {}
    project = spec_context.project_rel
    obligations: list[dict[str, Any]] = []
    if bool(final_config.get("require_manuscript", True)):
        obligations.append(
            {
                "id": "final-manuscript-exists",
                "type": "final_artifact",
                "claim": "A final manuscript artifact exists under the active project.",
                "source_file": spec_context.spec_rel,
                "route_id": "final",
                "project": project,
                "globs": final_config.get(
                    "manuscript_globs",
                    [
                        "final*.tex",
                        "final*.md",
                        "*manuscript*.tex",
                        "*manuscript*.md",
                        "*preprint*.tex",
                        "*preprint*.md",
                    ],
                ),
                "critical": True,
            }
        )
    if bool(final_config.get("require_pedagogical_note", True)):
        obligations.append(
            {
                "id": "final-pedagogical-note-exists",
                "type": "final_artifact",
                "claim": "A supplementary pedagogical note exists under the active project.",
                "source_file": spec_context.spec_rel,
                "route_id": "final",
                "project": project,
                "globs": final_config.get(
                    "pedagogical_note_globs",
                    [
                        "*pedagogical*.tex",
                        "*pedagogical*.md",
                        "*supplement*.tex",
                        "*supplement*.md",
                        "*appendix*.tex",
                        "*appendix*.md",
                    ],
                ),
                "critical": True,
            }
        )
    return obligations


def _default_critical(record: dict[str, Any]) -> bool:
    if "critical" in record:
        return bool(record.get("critical"))
    if _normalize_covers(record.get("covers_target_claims")):
        return True
    kind = str(record.get("type") or "").lower().replace("-", "_")
    return kind in {
        "algebra",
        "identity",
        "commutator",
        "operator_identity",
        "limit",
        "asymptotic",
        "dimension",
        "unit",
        "units",
        "unit_consistency",
        "citation",
        "literature",
        "assumption",
        "approximation",
        "final_artifact",
        "trace_preservation",
        "hermiticity",
        "tensor_index",
        "bch_order",
        "perturbation_order",
        "conservation",
    }


def _target_claim_status(
    spec_payload: dict[str, Any],
    obligations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    ids = target_claim_ids(spec_payload)
    status: list[dict[str, Any]] = []
    for claim_id in ids:
        covered_by = [
            str(item.get("id"))
            for item in obligations
            if item.get("status") == "passed"
            and claim_id in _normalize_covers(item.get("covers_target_claims"))
            and _counts_as_strong_target_coverage(item)
        ]
        claim_text = ""
        for claim in spec_payload.get("target_claims") or []:
            if isinstance(claim, dict) and str(claim.get("id")) == claim_id:
                claim_text = str(claim.get("claim") or "")
                break
        status.append(
            {
                "id": claim_id,
                "claim": claim_text,
                "status": "covered" if covered_by else "open",
                "covered_by": covered_by,
            }
        )
    return status


def _summarize(
    obligations: list[dict[str, Any]],
    target_status: list[dict[str, Any]],
    spec_integrity: dict[str, Any],
    *,
    review_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = {"passed": 0, "failed": 0, "open": 0, "unknown": 0}
    critical_failed = 0
    critical_unresolved = 0
    for item in obligations:
        status = str(item.get("status") or "open")
        if status not in counts:
            status = "open"
        counts[status] += 1
        if bool(item.get("critical")):
            if status == "failed":
                critical_failed += 1
            elif status in {"open", "unknown"}:
                critical_unresolved += 1
    target_claims_open = sum(
        1 for item in target_status if item.get("status") != "covered"
    )
    review_critical = sum(
        1 for item in review_findings if item.get("severity") == "critical"
    )
    review_warn = sum(1 for item in review_findings if item.get("severity") == "warn")
    spec_ok = bool(spec_integrity.get("ok"))
    final_ready = (
        spec_ok
        and bool(obligations)
        and critical_failed == 0
        and critical_unresolved == 0
        and target_claims_open == 0
        and review_critical == 0
    )
    return {
        "total": len(obligations),
        **counts,
        "critical_failed": critical_failed,
        "critical_unresolved": critical_unresolved,
        "target_claims_open": target_claims_open,
        "review_critical": review_critical,
        "review_warn": review_warn,
        "spec_integrity_ok": spec_ok,
        "final_ready": final_ready,
    }


def _review_findings(
    *,
    spec_context: DerivationSpecContext,
    obligations: list[dict[str, Any]],
    target_status: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not spec_context.integrity.get("ok"):
        findings.append(
            {
                "id": "spec-integrity-mismatch",
                "severity": "critical",
                "message": "Locked derivation spec hash does not match locked fields.",
            }
        )
    for item in target_status:
        if item.get("status") != "covered":
            weak_coverers = [
                str(obligation.get("id"))
                for obligation in obligations
                if obligation.get("status") == "passed"
                and item.get("id")
                in _normalize_covers(obligation.get("covers_target_claims"))
                and not _counts_as_strong_target_coverage(obligation)
            ]
            findings.append(
                {
                    "id": f"target-uncovered-{item.get('id')}",
                    "severity": "critical",
                    "message": (
                        "Target claim has no passed strong covering obligation."
                        + (
                            " Weak/self-certified coverers: " + ", ".join(weak_coverers)
                            if weak_coverers
                            else ""
                        )
                    ),
                }
            )
    explicit = [item for item in obligations if not bool(item.get("auto_extracted"))]
    if not explicit:
        findings.append(
            {
                "id": "no-explicit-obligations",
                "severity": "critical",
                "message": "No explicit proof obligations were provided.",
            }
        )
    for item in obligations:
        claim = str(item.get("claim") or "")
        if _HIDDEN_LEMMA_RE.search(claim) and not (
            item.get("citation")
            or item.get("source")
            or item.get("reference")
            or item.get("derived_here")
        ):
            findings.append(
                {
                    "id": f"hidden-lemma-{item.get('id')}",
                    "severity": "critical" if item.get("critical") else "warn",
                    "message": (
                        "Potential hidden lemma or unsupported standard-result claim "
                        "needs derivation or citation."
                    ),
                }
            )
    final_config = spec_context.payload.get("final_artifacts")
    if isinstance(final_config, dict) and bool(
        final_config.get("allow_missing", False)
    ):
        findings.append(
            {
                "id": "final-artifacts-relaxed",
                "severity": "warn",
                "message": "Spec relaxes final artifact requirements.",
            }
        )
    return findings


def _obligation_cache_key(obligation: dict[str, Any]) -> str:
    relevant_keys = [
        "type",
        "claim",
        "assumptions",
        "lhs",
        "rhs",
        "expression",
        "variable",
        "point",
        "expected",
        "lhs_dimension",
        "rhs_dimension",
        "observed_dimension",
        "expected_dimension",
        "citation",
        "source",
        "reference",
        "covers_target_claims",
        "route_id",
        "matrix",
        "terms",
        "max_order",
        "operators",
        "relations",
        "globs",
        "project",
        "path",
        "file",
        "latex_path",
        "pdf_path",
    ]
    payload = {key: obligation.get(key) for key in relevant_keys if key in obligation}
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _can_use_cached_result(obligation: dict[str, Any]) -> bool:
    if bool(obligation.get("force_revalidate")):
        return False
    kind = str(obligation.get("type") or "").lower().replace("-", "_")
    return kind in {
        "algebra",
        "identity",
        "commutator",
        "operator_identity",
        "limit",
        "asymptotic",
        "dimension",
        "unit",
        "units",
        "unit_consistency",
        "citation",
        "literature",
        "assumption",
        "approximation",
        "trace_preservation",
        "hermiticity",
        "tensor_index",
        "bch_order",
        "perturbation_order",
        "conservation",
    }


def _normalize_covers(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _counts_as_strong_target_coverage(item: dict[str, Any]) -> bool:
    if bool(item.get("auto_extracted")):
        return False
    kind = str(item.get("type") or "").strip().lower().replace("-", "_")
    return kind in _STRONG_TARGET_COVERAGE_TYPES


def _validator_name(obligation: dict[str, Any]) -> str:
    kind = str(obligation.get("type") or "").strip().lower().replace("-", "_")
    if kind in {"algebra", "identity", "limit", "asymptotic"}:
        return "sympy"
    if kind in {"commutator", "operator_identity"}:
        return "sympy_noncommutative"
    if kind in {"dimension", "unit", "units", "unit_consistency"}:
        return "dimension"
    if kind in {"numeric_check", "numerical_check", "numerical"}:
        return "numeric"
    if kind in {"latex_build", "latex"}:
        return "latex"
    if kind in {"formal_optional", "lean", "formal"}:
        return str(obligation.get("backend") or "lean")
    if kind in {"citation", "literature"}:
        return "citation"
    if kind in {"assumption", "approximation"}:
        return "assumption"
    if kind in {"final_artifact", "final_manuscript", "pedagogical_note"}:
        return "artifact"
    if kind in {
        "trace_preservation",
        "trace",
        "hermiticity",
        "hermitian",
        "tensor_index",
        "tensor_indices",
        "index_contraction",
        "bch_order",
        "perturbation_order",
        "order_bookkeeping",
        "conservation",
        "conservation_law",
    }:
        return "physics_domain"
    return "manual"


def _extract_display_equations(text: str) -> list[str]:
    equations: list[str] = []
    for pattern in _DISPLAY_MATH_RES:
        for match in pattern.finditer(text):
            body = " ".join(match.group(1).split())
            if body:
                equations.append(body)
    return equations


def _interesting_lines(text: str, pattern: re.Pattern[str]) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not stripped:
            continue
        if pattern.search(stripped):
            lines.append(stripped)
    return lines


def _auto_obligation(
    source_rel: str,
    *,
    kind: str,
    claim: str,
    obligation_type: str = "manual",
) -> dict[str, Any]:
    digest = hashlib.sha256(f"{source_rel}:{kind}:{claim}".encode("utf-8")).hexdigest()
    # Auto-extracted items are reviewer prompts. They should guide the next
    # agent turn, but final readiness must be gated by explicit obligations
    # unless the final manuscript itself contains an actual citation placeholder.
    critical = bool(obligation_type == "citation" and _is_final_like_source(source_rel))
    return {
        "id": f"auto-{kind}-{digest[:12]}",
        "type": obligation_type,
        "claim": claim,
        "source_file": source_rel,
        "route_id": _infer_route_id(source_rel),
        "critical": critical,
        "auto_extracted": True,
    }


def _is_final_like_source(source_rel: str) -> bool:
    stem = Path(source_rel).stem.lower()
    return any(token in stem for token in ("final", "manuscript", "preprint"))


def _infer_route_id(source_rel: str) -> str:
    path = Path(source_rel)
    parts = path.parts
    if len(parts) >= 3 and parts[0] == "projects":
        stem = Path(parts[-1]).stem
        if stem not in {"proof_obligations", "derivation_spec"}:
            return stem
        return parts[1]
    return Path(source_rel).stem or "default"


def _shorten(text: str, limit: int = 180) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _load_goal_cache(repo_dir: Path) -> dict[str, Any]:
    path = goal_cache_path_for(repo_dir)
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    goals = payload.get("goals") if isinstance(payload, dict) else None
    return goals if isinstance(goals, dict) else {}


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(path)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
