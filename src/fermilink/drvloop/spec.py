from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from fermilink.drvloop.prompts import (
    DRVLOOP_MEMORY_DIRNAME,
    DRVLOOP_SPEC_FILENAME,
    DRVLOOP_STATE_DIRNAME,
)


DRVLOOP_SESSION_FILENAME = "session.json"
_LOCKED_SPEC_FIELDS = (
    "problem_statement",
    "target_claims",
    "assumptions",
    "allowed_approximations",
    "domains",
    "boundary_conditions",
    "final_artifacts",
    "non_goals",
)


@dataclass(frozen=True)
class DerivationSpecContext:
    project_dir: Path
    spec_path: Path
    payload: dict[str, Any]
    integrity: dict[str, Any]

    @property
    def project_rel(self) -> str:
        return self.project_dir.as_posix()

    @property
    def spec_rel(self) -> str:
        return self.spec_path.as_posix()


def ensure_derivation_spec(
    *,
    repo_dir: Path,
    user_prompt: str,
    prompt_file: str | None,
) -> DerivationSpecContext:
    """Create or reuse the active drvloop derivation specification."""

    project_dir = _resolve_active_project_dir(repo_dir, user_prompt)
    project_dir.mkdir(parents=True, exist_ok=True)
    spec_path = project_dir / DRVLOOP_SPEC_FILENAME
    if spec_path.is_file():
        payload = _load_spec_payload(spec_path)
    else:
        payload = _default_spec_payload(
            user_prompt=user_prompt,
            prompt_file=prompt_file,
        )
        _write_spec_payload(spec_path, payload)

    integrity = validate_spec_integrity(payload)
    _save_active_project(repo_dir, project_dir)
    return DerivationSpecContext(
        project_dir=project_dir.relative_to(repo_dir),
        spec_path=spec_path.relative_to(repo_dir),
        payload=payload,
        integrity=integrity,
    )


def validate_spec_integrity(payload: dict[str, Any]) -> dict[str, Any]:
    expected = str(payload.get("locked_hash") or "").strip()
    locked_fields = _locked_fields_for(payload)
    actual = compute_locked_hash(payload, locked_fields=locked_fields)
    ok = bool(expected) and expected == actual
    return {
        "ok": ok,
        "expected_hash": expected,
        "actual_hash": actual,
        "locked_fields": list(locked_fields),
        "status": "ok" if ok else "mismatch",
    }


def compute_locked_hash(
    payload: dict[str, Any],
    *,
    locked_fields: tuple[str, ...] | None = None,
) -> str:
    fields = locked_fields or _locked_fields_for(payload)
    locked = {key: payload.get(key) for key in fields}
    canonical = json.dumps(
        locked, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def format_spec_context(context: DerivationSpecContext) -> str:
    payload = context.payload
    target_lines = []
    for claim in _as_list(payload.get("target_claims")):
        if isinstance(claim, dict):
            claim_id = str(claim.get("id") or "").strip() or "target"
            text = str(claim.get("claim") or "").strip()
            target_lines.append(f"- {claim_id}: {text}")
        elif str(claim).strip():
            target_lines.append(f"- {str(claim).strip()}")
    if not target_lines:
        target_lines = ["- No target claims recorded."]

    assumption_lines = _string_list_lines(payload.get("assumptions"))
    approximation_lines = _string_list_lines(payload.get("allowed_approximations"))
    domain_lines = _string_list_lines(payload.get("domains"))

    integrity = context.integrity
    return "\n".join(
        [
            f"- project: {context.project_rel}",
            f"- spec: {context.spec_rel}",
            f"- locked_hash_status: {integrity.get('status')}",
            "- target_claims:",
            *target_lines,
            "- assumptions:",
            *assumption_lines,
            "- allowed_approximations:",
            *approximation_lines,
            "- domains:",
            *domain_lines,
        ]
    )


def target_claim_ids(payload: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for index, claim in enumerate(_as_list(payload.get("target_claims")), start=1):
        if isinstance(claim, dict):
            claim_id = str(claim.get("id") or "").strip()
            if claim_id:
                ids.append(claim_id)
                continue
        ids.append(f"target-{index}")
    return ids


def _locked_fields_for(payload: dict[str, Any]) -> tuple[str, ...]:
    raw = payload.get("locked_fields")
    if isinstance(raw, list):
        fields = tuple(str(item).strip() for item in raw if str(item).strip())
        if fields:
            return fields
    return _LOCKED_SPEC_FIELDS


def _resolve_active_project_dir(repo_dir: Path, user_prompt: str) -> Path:
    session_project = _load_active_project(repo_dir)
    if session_project is not None:
        candidate = repo_dir / session_project
        if candidate.is_dir():
            return candidate

    projects_dir = repo_dir / DRVLOOP_MEMORY_DIRNAME
    specs = []
    if projects_dir.is_dir():
        for path in projects_dir.glob(f"*/{DRVLOOP_SPEC_FILENAME}"):
            try:
                specs.append((path.stat().st_mtime_ns, path.parent))
            except OSError:
                continue
    if specs:
        return sorted(specs, reverse=True)[0][1]

    slug = _slugify(user_prompt)
    today = datetime.now(timezone.utc).date().isoformat()
    base = projects_dir / f"{today}-{slug}"
    candidate = base
    index = 2
    while candidate.exists():
        candidate = Path(f"{base}-{index}")
        index += 1
    return candidate


def _default_spec_payload(
    *, user_prompt: str, prompt_file: str | None
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "created_at_utc": now,
        "updated_at_utc": now,
        "prompt_source": prompt_file,
        "problem_statement": user_prompt.strip(),
        "target_claims": [
            {
                "id": "target-1",
                "claim": user_prompt.strip(),
            }
        ],
        "definitions": [],
        "assumptions": [],
        "allowed_approximations": [],
        "domains": [],
        "boundary_conditions": [],
        "final_artifacts": {
            "require_manuscript": True,
            "require_pedagogical_note": True,
            "manuscript_globs": [
                "final*.tex",
                "final*.md",
                "*manuscript*.tex",
                "*manuscript*.md",
                "*preprint*.tex",
                "*preprint*.md",
            ],
            "pedagogical_note_globs": [
                "*pedagogical*.tex",
                "*pedagogical*.md",
                "*supplement*.tex",
                "*supplement*.md",
                "*appendix*.tex",
                "*appendix*.md",
            ],
        },
        "non_goals": [],
        "spec_amendments": [],
        "locked_fields": list(_LOCKED_SPEC_FIELDS),
    }
    payload["locked_hash"] = compute_locked_hash(payload)
    return payload


def _load_spec_payload(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        payload = None
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("schema_version", 1)
    payload.setdefault("target_claims", [])
    payload.setdefault("assumptions", [])
    payload.setdefault("allowed_approximations", [])
    payload.setdefault("domains", [])
    payload.setdefault("boundary_conditions", [])
    payload.setdefault("final_artifacts", {})
    payload.setdefault("non_goals", [])
    payload.setdefault("spec_amendments", [])
    return payload


def _write_spec_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _load_active_project(repo_dir: Path) -> Path | None:
    path = repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_SESSION_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    rel = str(payload.get("active_project") or "").strip()
    if not rel:
        return None
    candidate = Path(rel)
    if candidate.is_absolute() or ".." in candidate.parts:
        return None
    return candidate


def _save_active_project(repo_dir: Path, project_dir: Path) -> None:
    state_dir = repo_dir / DRVLOOP_STATE_DIRNAME
    state_dir.mkdir(parents=True, exist_ok=True)
    try:
        rel = project_dir.relative_to(repo_dir).as_posix()
    except ValueError:
        rel = project_dir.as_posix()
    path = state_dir / DRVLOOP_SESSION_FILENAME
    path.write_text(
        json.dumps({"version": 1, "active_project": rel}, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )


def _slugify(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())[:8]
    if not words:
        return "derivation"
    slug = "-".join(words)
    return slug[:64].strip("-") or "derivation"


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _string_list_lines(value: Any) -> list[str]:
    items = _as_list(value)
    lines = [f"- {str(item).strip()}" for item in items if str(item).strip()]
    return lines or ["- None recorded."]
