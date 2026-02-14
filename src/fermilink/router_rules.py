from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from fermilink.package_registry import load_registry, normalize_package_id


DEFAULT_ROUTER_RULES_FILENAME = "router_rules.json"
FAMILY_HINTS_PATH = Path(__file__).resolve().parent / "data" / "router" / "family_hints.json"


def dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        lowered = item.strip().lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        result.append(lowered)
    return result


def normalize_terms(raw: Any) -> list[str]:
    if isinstance(raw, str):
        return dedupe(raw.split(","))
    if isinstance(raw, list):
        return dedupe([item for item in raw if isinstance(item, str)])
    return []


def package_id_terms(package_id: str) -> list[str]:
    lowered = package_id.lower()
    terms = [lowered, lowered.replace("-", " "), lowered.replace("_", " ")]
    parts: list[str] = []
    for chunk in lowered.replace("_", "-").split("-"):
        if chunk and len(chunk) >= 4 and not chunk.isdigit():
            parts.append(chunk)
    terms.extend(parts)
    return dedupe(terms)


@functools.lru_cache(maxsize=1)
def load_family_hints() -> dict[str, dict[str, list[str] | str]]:
    try:
        payload = json.loads(FAMILY_HINTS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid router family hints file: {FAMILY_HINTS_PATH}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Family hints payload must be a JSON object: {FAMILY_HINTS_PATH}")
    schema_version = payload.get("schema_version")
    legacy_version = payload.get("version")
    if schema_version is not None and not isinstance(schema_version, int):
        raise ValueError(f"Family hints payload has invalid schema_version: {FAMILY_HINTS_PATH}")
    if legacy_version is not None and not isinstance(legacy_version, int):
        raise ValueError(f"Family hints payload has invalid version: {FAMILY_HINTS_PATH}")
    families_raw = payload.get("families")
    if not isinstance(families_raw, dict):
        raise ValueError(f"Family hints payload missing `families` map: {FAMILY_HINTS_PATH}")

    parsed: dict[str, dict[str, list[str] | str]] = {}
    for family, raw_terms in families_raw.items():
        family_id = str(family).strip().lower()
        if not family_id:
            continue
        if not isinstance(raw_terms, dict):
            continue
        description_raw = raw_terms.get("description")
        description = (
            description_raw.strip()
            if isinstance(description_raw, str) and description_raw.strip()
            else f"Routing hints for {family_id} workflows."
        )
        parsed[family_id] = {
            "description": description,
            "strong_keywords": normalize_terms(raw_terms.get("strong_keywords")),
            "keywords": normalize_terms(raw_terms.get("keywords")),
            "negative_keywords": normalize_terms(raw_terms.get("negative_keywords")),
            "package_id_overrides": normalize_terms(raw_terms.get("package_id_overrides")),
        }
    return parsed


def infer_rule(package_id: str) -> dict[str, list[str]]:
    keywords = package_id_terms(package_id)
    strong_keywords: list[str] = []
    negative_keywords: list[str] = []

    lowered = package_id.lower()
    for family, payload in load_family_hints().items():
        package_id_overrides = payload.get("package_id_overrides")
        override_ids = (
            [item for item in package_id_overrides if isinstance(item, str)]
            if isinstance(package_id_overrides, list)
            else []
        )
        if family not in lowered and lowered not in override_ids:
            continue
        strong_keywords.extend(payload.get("strong_keywords", []))
        keywords.extend(payload.get("keywords", []))
        negative_keywords.extend(payload.get("negative_keywords", []))

    return {
        "strong_keywords": dedupe(strong_keywords),
        "keywords": dedupe(keywords),
        "negative_keywords": dedupe(negative_keywords),
    }


def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


def build_synced_rules(
    registry: dict[str, Any],
    existing_rules: dict[str, Any] | None,
    *,
    default_package_id: str | None = None,
    min_score: int | None = None,
    min_margin: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    packages_raw = registry.get("packages", {})
    installed_ids: list[str] = []
    if isinstance(packages_raw, dict):
        for raw_id in packages_raw.keys():
            try:
                installed_ids.append(normalize_package_id(str(raw_id)))
            except Exception:
                continue
    installed_ids = sorted(set(installed_ids))
    installed_set = set(installed_ids)

    existing = existing_rules if isinstance(existing_rules, dict) else {}
    existing_packages = existing.get("packages", {})
    if not isinstance(existing_packages, dict):
        existing_packages = {}

    synced_packages: dict[str, Any] = {}
    added: list[str] = []
    preserved: list[str] = []

    for package_id in installed_ids:
        raw_rule = existing_packages.get(package_id)
        if isinstance(raw_rule, dict):
            synced_packages[package_id] = {
                "strong_keywords": normalize_terms(raw_rule.get("strong_keywords")),
                "keywords": normalize_terms(raw_rule.get("keywords")),
                "negative_keywords": normalize_terms(raw_rule.get("negative_keywords")),
            }
            preserved.append(package_id)
        else:
            synced_packages[package_id] = infer_rule(package_id)
            added.append(package_id)

    removed = sorted(
        package_id for package_id in existing_packages.keys() if package_id not in installed_set
    )

    active_raw = registry.get("active_package")
    if isinstance(active_raw, str):
        try:
            active_id = normalize_package_id(active_raw)
        except Exception:
            active_id = None
    else:
        active_id = None
    if active_id not in installed_set:
        active_id = None

    existing_default_raw = existing.get("default_package_id")
    if isinstance(existing_default_raw, str):
        try:
            existing_default = normalize_package_id(existing_default_raw)
        except Exception:
            existing_default = None
    else:
        existing_default = None

    if default_package_id is not None:
        try:
            chosen_default = normalize_package_id(default_package_id)
        except Exception:
            chosen_default = None
    elif existing_default in installed_set:
        chosen_default = existing_default
    elif active_id in installed_set:
        chosen_default = active_id
    else:
        chosen_default = installed_ids[0] if installed_ids else None

    if chosen_default not in installed_set:
        chosen_default = None

    existing_min_score = existing.get("min_score")
    existing_min_margin = existing.get("min_margin")
    final_min_score = (
        min_score if isinstance(min_score, int) else (existing_min_score if isinstance(existing_min_score, int) else 2)
    )
    final_min_margin = (
        min_margin
        if isinstance(min_margin, int)
        else (existing_min_margin if isinstance(existing_min_margin, int) else 1)
    )

    payload = {
        "default_package_id": chosen_default,
        "min_score": final_min_score,
        "min_margin": final_min_margin,
        "packages": synced_packages,
    }
    summary = {
        "installed_count": len(installed_ids),
        "added_rules": added,
        "preserved_rules": preserved,
        "removed_rules": removed,
        "default_package_id": chosen_default,
    }
    return payload, summary


def sync_router_rules(
    scipkg_root: Path,
    *,
    router_rules_filename: str = DEFAULT_ROUTER_RULES_FILENAME,
    default_package_id: str | None = None,
    min_score: int | None = None,
    min_margin: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    registry = load_registry(scipkg_root)
    router_rules_path = scipkg_root / router_rules_filename

    try:
        existing_rules = _load_json(router_rules_path)
    except Exception:
        existing_rules = None

    payload, summary = build_synced_rules(
        registry=registry,
        existing_rules=existing_rules if isinstance(existing_rules, dict) else None,
        default_package_id=default_package_id,
        min_score=min_score,
        min_margin=min_margin,
    )

    if not dry_run:
        _write_json(router_rules_path, payload)

    return {
        "scipkg_root": str(scipkg_root),
        "router_rules_path": str(router_rules_path),
        "dry_run": dry_run,
        "summary": summary,
        "payload": payload,
    }
