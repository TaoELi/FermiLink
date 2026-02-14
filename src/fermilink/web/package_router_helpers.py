from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

PACKAGE_FAMILY_RULES: dict[str, dict[str, list[str]]] = {
    "maxwelllink": {
        "strong_keywords": [
            "maxwelllink",
            "light matter",
            "maxwell bloch",
            "quantum optics",
            "radiative decay",
            "driven two level",
            "spontaneous emission",
            "two level system",
            "two-level system",
            "weakly excited",
            "quantum emitter",
        ],
        "keywords": [
            "electromagnetic solver",
            "open quantum",
            "photonics",
            "maxwell equation",
            "coupled light",
            "population dynamics",
            "density matrix",
            "dephasing",
            "purcell",
            "vacuum coupling",
        ],
        "negative_keywords": ["classical md", "force field"],
    },
    "meep": {
        "strong_keywords": [
            "meep",
            "fdtd",
            "finite difference time domain",
            "electromagnetic wave propagation",
        ],
        "keywords": [
            "dielectric",
            "waveguide",
            "photonic crystal",
            "pml",
            "harminv",
        ],
        "negative_keywords": [
            "gaussian",
            "qchem",
            "lammps",
            "gromacs",
            "spontaneous emission",
            "two level system",
            "two-level system",
            "weakly excited",
            "density matrix",
            "population dynamics",
            "maxwell bloch",
        ],
    },
    "qchem": {
        "strong_keywords": [
            "qchem",
            "q-chem",
            "electronic structure",
            "dft",
            "ab initio",
            "hartree fock",
        ],
        "keywords": [
            "basis set",
            "scf",
            "td-dft",
            "coupled cluster",
            "quantum chemistry",
        ],
        "negative_keywords": ["md", "gromacs", "lammps", "fdtd"],
    },
    "gaussian": {
        "strong_keywords": [
            "gaussian",
            "gaussian16",
            "g16",
            "electronic structure",
            "quantum chemistry",
        ],
        "keywords": ["basis set", "opt freq", "pcm", "scf", "dft"],
        "negative_keywords": ["md", "lammps", "gromacs", "fdtd"],
    },
    "lammps": {
        "strong_keywords": [
            "lammps",
            "classical md",
            "molecular dynamics",
            "force field",
        ],
        "keywords": ["pair style", "thermo", "nvt", "npt", "dump"],
        "negative_keywords": ["td-dft", "gaussian", "qchem", "fdtd"],
    },
    "gromacs": {
        "strong_keywords": [
            "gromacs",
            "mdp",
            "gmx",
            "classical md",
            "molecular dynamics",
        ],
        "keywords": ["topol", "gro", "xtc", "nvt", "npt"],
        "negative_keywords": ["td-dft", "gaussian", "qchem", "fdtd"],
    },
}


def _normalize_package_id_safe(
    value: str | None, *, normalize_package_id: Callable[[str], str]
) -> str | None:
    """Normalize package id and suppress validation exceptions."""

    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return normalize_package_id(value)
    except Exception:
        return None


def _dedupe_terms(values: list[str]) -> list[str]:
    """Deduplicate string terms while preserving order."""

    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        term = value.strip().lower()
        if not term or term in seen:
            continue
        seen.add(term)
        result.append(term)
    return result


def _normalize_rule_terms(raw: Any) -> list[str]:
    """Normalize router term payload to a list of unique strings."""

    if isinstance(raw, str):
        return _dedupe_terms(raw.split(","))
    if isinstance(raw, list):
        return _dedupe_terms([item for item in raw if isinstance(item, str)])
    return []


def _load_router_config(
    scipkg_root: Path,
    *,
    package_router_min_score: int,
    package_router_min_margin: int,
    package_router_rules_filename: str,
    normalize_package_id_safe: Callable[[str | None], str | None],
    logger: logging.Logger,
) -> dict[str, Any]:
    """Load package-router config from `scientific_packages/router_rules.json`."""

    default_config: dict[str, Any] = {
        "default_package_id": None,
        "min_score": package_router_min_score,
        "min_margin": package_router_min_margin,
        "packages": {},
    }
    path = scipkg_root / package_router_rules_filename
    if not path.is_file():
        return default_config
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read router rules %s: %s", path, exc)
        return default_config
    if not isinstance(loaded, dict):
        return default_config

    config = dict(default_config)
    default_id = normalize_package_id_safe(loaded.get("default_package_id"))
    if default_id:
        config["default_package_id"] = default_id

    min_score_raw = loaded.get("min_score")
    if isinstance(min_score_raw, int):
        config["min_score"] = min_score_raw

    min_margin_raw = loaded.get("min_margin")
    if isinstance(min_margin_raw, int):
        config["min_margin"] = min_margin_raw

    packages_raw = loaded.get("packages")
    if isinstance(packages_raw, dict):
        normalized_packages: dict[str, Any] = {}
        for raw_id, payload in packages_raw.items():
            if not isinstance(payload, dict):
                continue
            normalized_id = normalize_package_id_safe(str(raw_id))
            if not normalized_id:
                continue
            normalized_packages[normalized_id] = payload
        config["packages"] = normalized_packages
    return config


def _package_id_terms(package_id: str) -> list[str]:
    """Build fallback match terms from a package id."""

    lowered = package_id.lower()
    terms = [
        lowered,
        lowered.replace("-", " "),
        lowered.replace("_", " "),
        lowered.replace("_", "-"),
        lowered.replace("-", "_"),
    ]
    parts = re.split(r"[-_]+", lowered)
    for part in parts:
        if len(part) >= 4 and not part.isdigit():
            terms.append(part)
    return _dedupe_terms(terms)


def _build_package_rule(
    package_id: str,
    config_packages: dict[str, Any],
    *,
    package_family_rules: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, list[str]]:
    """Build effective router rule for one installed package."""

    family_rules = package_family_rules or PACKAGE_FAMILY_RULES
    base_keywords = _package_id_terms(package_id)
    base_strong: list[str] = []
    base_negative: list[str] = []

    lowered = package_id.lower()
    for family, payload in family_rules.items():
        if family not in lowered:
            continue
        base_keywords.extend(payload.get("keywords", []))
        base_strong.extend(payload.get("strong_keywords", []))
        base_negative.extend(payload.get("negative_keywords", []))

    configured = config_packages.get(package_id)
    configured_keywords: list[str] = []
    configured_strong: list[str] = []
    configured_negative: list[str] = []
    if isinstance(configured, dict):
        configured_keywords = _normalize_rule_terms(configured.get("keywords"))
        configured_strong = _normalize_rule_terms(configured.get("strong_keywords"))
        configured_negative = _normalize_rule_terms(configured.get("negative_keywords"))

    return {
        "keywords": _dedupe_terms(base_keywords + configured_keywords),
        "strong_keywords": _dedupe_terms(base_strong + configured_strong),
        "negative_keywords": _dedupe_terms(base_negative + configured_negative),
    }


def _match_term_count(text: str, terms: list[str]) -> int:
    """Count unique matched terms in normalized text."""

    hits = 0
    for term in terms:
        if not term:
            continue
        if re.fullmatch(r"[a-z0-9]+", term):
            pattern = rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])"
            if re.search(pattern, text):
                hits += 1
        elif term in text:
            hits += 1
    return hits


def _route_package_candidate(
    user_text: str,
    package_ids: list[str],
    current_package_id: str | None,
    config: dict[str, Any],
    *,
    package_router_min_score: int,
    package_router_min_margin: int,
    package_family_rules: dict[str, dict[str, list[str]]] | None = None,
) -> dict[str, Any]:
    """Route one user request to the best package candidate."""

    text = (user_text or "").strip().lower()
    if not text:
        return {
            "selected_package_id": None,
            "reason": "empty_prompt",
            "scores": [],
            "margin": 0,
        }
    if not package_ids:
        return {
            "selected_package_id": None,
            "reason": "no_packages",
            "scores": [],
            "margin": 0,
        }

    config_packages = config.get("packages", {})
    if not isinstance(config_packages, dict):
        config_packages = {}

    scored: list[dict[str, Any]] = []
    for package_id in package_ids:
        rule = _build_package_rule(
            package_id,
            config_packages,
            package_family_rules=package_family_rules,
        )
        strong_hits = _match_term_count(text, rule["strong_keywords"])
        keyword_hits = _match_term_count(text, rule["keywords"])
        negative_hits = _match_term_count(text, rule["negative_keywords"])

        score = strong_hits * 3 + keyword_hits - negative_hits * 2
        if package_id in text:
            score += 3

        scored.append(
            {
                "package_id": package_id,
                "score": score,
                "strong_hits": strong_hits,
                "keyword_hits": keyword_hits,
                "negative_hits": negative_hits,
            }
        )

    scored.sort(key=lambda item: (item["score"], item["package_id"]), reverse=True)
    top = scored[0]
    second = scored[1] if len(scored) > 1 else None

    min_score_raw = config.get("min_score", package_router_min_score)
    min_margin_raw = config.get("min_margin", package_router_min_margin)
    min_score = int(min_score_raw) if isinstance(min_score_raw, int) else 0
    min_margin = int(min_margin_raw) if isinstance(min_margin_raw, int) else 0

    margin = top["score"] - second["score"] if second else top["score"]
    if top["score"] < min_score:
        return {
            "selected_package_id": None,
            "reason": "low_score",
            "scores": scored,
            "margin": margin,
        }

    if second is not None and margin < min_margin:
        if current_package_id and current_package_id == top["package_id"]:
            return {
                "selected_package_id": current_package_id,
                "reason": "ambiguous_keep_current",
                "scores": scored,
                "margin": margin,
            }
        return {
            "selected_package_id": None,
            "reason": "ambiguous",
            "scores": scored,
            "margin": margin,
        }

    return {
        "selected_package_id": top["package_id"],
        "reason": "matched",
        "scores": scored,
        "margin": margin,
    }


def _resolve_default_package_id(
    package_ids: list[str],
    active_package_id: str | None,
    config: dict[str, Any],
    *,
    normalize_package_id_safe: Callable[[str | None], str | None],
) -> str | None:
    """Resolve default package from config, registry active package, or installed list."""

    if not package_ids:
        return None
    package_set = set(package_ids)
    config_default = normalize_package_id_safe(config.get("default_package_id"))
    if config_default in package_set:
        return config_default
    if active_package_id in package_set:
        return active_package_id
    return package_ids[0]


def _build_package_catalog(
    package_ids: list[str],
    active_package_id: str | None,
    scipkg_root: Path,
    *,
    load_registry: Callable[[Path], dict[str, Any]],
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """Build concise package catalog for preflight routing checks."""

    catalog: list[dict[str, Any]] = []
    packages_payload: dict[str, Any] = {}
    try:
        registry = load_registry(scipkg_root)
        maybe_packages = registry.get("packages", {})
        if isinstance(maybe_packages, dict):
            packages_payload = maybe_packages
    except Exception as exc:
        logger.warning("Failed to load package catalog metadata: %s", exc)
        packages_payload = {}

    for package_id in package_ids:
        meta_raw = packages_payload.get(package_id)
        meta = meta_raw if isinstance(meta_raw, dict) else {}
        item: dict[str, Any] = {
            "id": package_id,
            "active": package_id == active_package_id,
        }

        title = meta.get("title")
        if isinstance(title, str) and title and title != package_id:
            item["title"] = title

        source = meta.get("source")
        if isinstance(source, str) and source:
            item["source"] = source

        overlay_entries = meta.get("overlay_entries")
        if isinstance(overlay_entries, list):
            normalized_entries = [
                str(entry).strip()
                for entry in overlay_entries
                if isinstance(entry, str) and str(entry).strip()
            ]
            if normalized_entries:
                item["overlay_entries"] = normalized_entries

        dependency_package_ids = meta.get("dependency_package_ids")
        if isinstance(dependency_package_ids, list):
            normalized_dependency_ids = [
                str(entry).strip()
                for entry in dependency_package_ids
                if isinstance(entry, str) and str(entry).strip()
            ]
            if normalized_dependency_ids:
                item["dependency_package_ids"] = normalized_dependency_ids

        catalog.append(item)
    return catalog


def _build_second_guess_prompt(
    *,
    user_text: str,
    current_package_id: str | None,
    package_catalog: list[dict[str, Any]],
) -> str:
    """Create routing preflight prompt for Codex second-guess decision."""

    catalog_json = json.dumps(package_catalog, indent=2, ensure_ascii=False)
    current_label = current_package_id if current_package_id else "none"
    return (
        "PACKAGE ROUTING PREFLIGHT ONLY.\n"
        "You must decide whether the currently selected scientific package is suitable.\n"
        "Read AGENTS.md in the repo root and follow its Package Routing Policy.\n"
        "Do NOT run shell commands. Do NOT edit files. Do NOT create outputs.\n"
        "Return exactly one JSON object and nothing else.\n\n"
        "Required JSON schema:\n"
        "{\n"
        '  "route": "keep" | "switch",\n'
        '  "package_id": "<installed_package_id_or_null>",\n'
        '  "confidence": <number_between_0_and_1>,\n'
        '  "reason": "<short_reason>"\n'
        "}\n\n"
        f"Current package: {current_label}\n"
        f"Installed package catalog:\n{catalog_json}\n\n"
        f"User request:\n{(user_text or '').strip()}\n"
    )


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Extract first valid JSON object from text."""

    if not text:
        return None

    start_positions = [idx for idx, char in enumerate(text) if char == "{"]
    for start in start_positions:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
                    break
                if depth < 0:
                    break
    return None


def _coerce_confidence(value: Any) -> float:
    """Normalize confidence value to [0, 1]."""

    if isinstance(value, (int, float)):
        confidence = float(value)
    elif isinstance(value, str):
        try:
            confidence = float(value.strip())
        except ValueError:
            return 0.0
    else:
        return 0.0

    if confidence < 0:
        return 0.0
    if confidence > 1:
        return 1.0
    return confidence


def _resolve_package_alias(
    raw_target: str,
    package_ids: list[str],
    *,
    normalize_package_id_safe: Callable[[str | None], str | None],
) -> str | None:
    """Resolve flexible package id input to one installed package id."""

    normalized = normalize_package_id_safe(raw_target)
    if normalized and normalized in package_ids:
        return normalized

    lowered = (raw_target or "").strip().lower()
    if not lowered:
        return None

    exact_matches = [package_id for package_id in package_ids if package_id == lowered]
    if len(exact_matches) == 1:
        return exact_matches[0]

    prefix_matches = [
        package_id for package_id in package_ids if package_id.startswith(lowered)
    ]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    contains_matches = [package_id for package_id in package_ids if lowered in package_id]
    if len(contains_matches) == 1:
        return contains_matches[0]
    return None


def _parse_package_command(
    content: str,
    *,
    package_command_prefix: str,
) -> tuple[str, list[str]] | None:
    """Parse `/package` chat commands."""

    text = (content or "").strip()
    if not text:
        return None
    if not text.lower().startswith(package_command_prefix):
        return None

    parts = text.split()
    if len(parts) == 1:
        return "help", []

    subcommand = parts[1].strip().lower()
    args = parts[2:]
    if subcommand in {"list", "ls"}:
        return "list", args
    if subcommand in {"current", "status"}:
        return "current", args
    if subcommand in {"use", "set"}:
        return "use", args
    if subcommand in {"clear", "reset"}:
        return "clear", args
    if subcommand == "auto":
        return "auto", args
    if subcommand == "help":
        return "help", args

    # Support shorthand: `/package <package-id>`
    return "use", parts[1:]


def _format_package_list(
    package_ids: list[str],
    active_package_id: str | None,
    current_package_id: str | None,
) -> str:
    """Format installed package list for chat output."""

    if not package_ids:
        return "No installed scientific packages found."

    lines = ["Installed packages:"]
    for package_id in package_ids:
        labels: list[str] = []
        if package_id == current_package_id:
            labels.append("current")
        if package_id == active_package_id:
            labels.append("registry-active")
        suffix = f" ({', '.join(labels)})" if labels else ""
        lines.append(f"- `{package_id}`{suffix}")
    return "\n".join(lines)
