from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fermilink.package_registry import load_registry, normalize_package_id


DEFAULT_ROUTER_RULES_FILENAME = "router_rules.json"

FAMILY_HINTS: dict[str, dict[str, list[str]]] = {
    "maxwelllink": {
        "strong_keywords": [
            "maxwelllink",
            "light matter",
            "maxwell bloch",
            "quantum optics",
            "spontaneous emission",
            "two level system",
            "two-level system",
            "weakly excited",
            "quantum emitter",
        ],
        "keywords": [
            "radiative decay",
            "driven two level",
            "photonics",
            "population dynamics",
            "density matrix",
            "dephasing",
            "purcell",
            "vacuum coupling",
        ],
        "negative_keywords": ["gromacs"],
    },
    "meep": {
        "strong_keywords": ["meep", "fdtd", "finite difference time domain"],
        "keywords": ["waveguide", "pml", "photonic crystal", "dielectric"],
        "negative_keywords": [
            "qchem",
            "gaussian",
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
        "strong_keywords": ["qchem", "q-chem", "electronic structure", "dft"],
        "keywords": ["ab initio", "hartree fock", "basis set", "scf"],
        "negative_keywords": ["fdtd", "lammps", "gromacs"],
    },
    "gaussian": {
        "strong_keywords": ["gaussian", "gaussian16", "g16", "electronic structure"],
        "keywords": ["quantum chemistry", "basis set", "scf", "dft"],
        "negative_keywords": ["fdtd", "lammps", "gromacs"],
    },
    "elk": {
        "strong_keywords": [
            "elk",
            "electronic structure",
            "density functional theory",
            "fp-lapw",
        ],
        "keywords": [
            "all-electron",
            "full-potential linearized augmented-plane wave",
            "band structure",
            "k-point",
            "muffin-tin",
            "dft",
            "scf",
        ],
        "negative_keywords": ["fdtd", "lammps", "gromacs"],
    },
    "lammps": {
        "strong_keywords": ["lammps", "classical md", "molecular dynamics"],
        "keywords": ["force field", "pair style", "nvt", "npt"],
        "negative_keywords": ["qchem", "gaussian", "fdtd"],
    },
    "ase": {
        "strong_keywords": ["ase", "atomic simulation environment", "atoms object"],
        "keywords": [
            "calculator",
            "geometry optimization",
            "structure relaxation",
            "build surface",
            "neb",
            "trajectory",
        ],
        "negative_keywords": ["fdtd", "maxwell bloch", "gromacs"],
    },
    "qutip": {
        "strong_keywords": [
            "qutip",
            "quantum toolbox in python",
            "lindblad",
            "master equation",
        ],
        "keywords": [
            "open quantum system",
            "hamiltonian",
            "collapse operator",
            "density matrix",
            "bloch sphere",
            "time evolution",
        ],
        "negative_keywords": ["fdtd", "lammps", "gromacs"],
    },
    "kwant": {
        "strong_keywords": [
            "kwant",
            "quantum transport",
            "tight-binding",
            "tight binding",
            "scattering matrix",
        ],
        "keywords": [
            "conductance",
            "greens function",
            "wave function",
            "dispersion relation",
            "band structure",
            "builder",
            "smatrix",
            "quantum hall",
            "topological insulator",
        ],
        "negative_keywords": [
            "fdtd",
            "finite difference time domain",
            "maxwell bloch",
            "qchem",
            "gaussian",
            "hartree fock",
            "molecular dynamics",
            "lammps",
            "gromacs",
        ],
    },
    "tkwant": {
        "strong_keywords": [
            "tkwant",
            "time-dependent quantum transport",
            "time dependent quantum transport",
        ],
        "keywords": [
            "time-dependent quantum dynamics",
            "mesoscopic systems",
            "time-dependent generalization of kwant",
            "transient quantum transport",
            "nonequilibrium dynamics",
            "kwant",
        ],
        "negative_keywords": [
            "fdtd",
            "finite difference time domain",
            "maxwell bloch",
            "qchem",
            "gaussian",
            "hartree fock",
            "classical md",
            "molecular dynamics",
            "lammps",
            "gromacs",
        ],
    },
    "oqupy": {
        "strong_keywords": [
            "oqupy",
            "open quantum systems in python",
            "process tensor",
            "non-markovian open quantum systems",
            "pt-tempo",
            "pt-tebd",
        ],
        "keywords": [
            "non-markovian",
            "open quantum system",
            "tempo",
            "pt_tempo",
            "pt_tebd",
            "multi-time correlations",
            "bath correlations",
            "tensor network",
            "spin-boson",
            "gibbs tempo",
        ],
        "negative_keywords": [
            "fdtd",
            "finite difference time domain",
            "waveguide",
            "photonic crystal",
            "electronic structure",
            "quantum chemistry",
            "hartree fock",
            "basis set",
            "classical md",
            "molecular dynamics",
            "force field",
            "lammps",
            "gromacs",
        ],
    },
    "psi4": {
        "strong_keywords": ["psi4", "electronic structure", "quantum chemistry", "ab initio"],
        "keywords": ["scf", "mp2", "ccsd", "basis set", "hartree fock", "dft"],
        "negative_keywords": ["fdtd", "lammps", "gromacs", "pyscf"],
    },
    "pyscf": {
        "strong_keywords": [
            "pyscf",
            "python-based simulations of chemistry framework",
            "electronic structure",
            "quantum chemistry",
        ],
        "keywords": [
            "scf",
            "hartree fock",
            "dft",
            "mp2",
            "ccsd",
            "casscf",
            "active space",
            "basis set",
            "molecular orbital",
            "density fitting",
        ],
        "negative_keywords": ["fdtd", "lammps", "gromacs", "psi4"],
    },
    "gromacs": {
        "strong_keywords": ["gromacs", "gmx", "classical md", "molecular dynamics"],
        "keywords": ["mdp", "topol", "nvt", "npt"],
        "negative_keywords": ["qchem", "gaussian", "fdtd"],
    },
    "packmol": {
        "strong_keywords": [
            "packmol",
            "initial configurations for molecular dynamics simulations",
            "spatial constraints",
        ],
        "keywords": [
            "molecule packing",
            "solvation box",
            "lipid bilayer",
            "pdb",
            "xyz",
            "inside box",
            "outside sphere",
            "tolerance",
        ],
        "negative_keywords": ["qchem", "gaussian", "fdtd", "meep", "qutip"],
    },
}


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


def infer_rule(package_id: str) -> dict[str, list[str]]:
    keywords = package_id_terms(package_id)
    strong_keywords: list[str] = []
    negative_keywords: list[str] = []

    lowered = package_id.lower()
    for family, payload in FAMILY_HINTS.items():
        if family not in lowered:
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
