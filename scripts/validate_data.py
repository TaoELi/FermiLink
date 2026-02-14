#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _validate_term_list(raw: Any, *, field_name: str, context: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{context}.{field_name} must be a list of strings.")
    terms: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not _is_nonempty_str(item):
            raise ValueError(
                f"{context}.{field_name}[{index}] must be a non-empty string."
            )
        normalized = item.strip().lower()
        if normalized in seen:
            raise ValueError(
                f"{context}.{field_name} contains duplicate term: {item!r}."
            )
        seen.add(normalized)
        terms.append(item.strip())
    return terms


def validate_curated_channel(path: Path) -> tuple[list[str], set[str]]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object.")

    schema_version = payload.get("schema_version", 1)
    if not isinstance(schema_version, int):
        raise ValueError(f"{path} schema_version must be an integer.")
    if schema_version not in {1, 2}:
        raise ValueError(f"{path} schema_version {schema_version} is unsupported.")

    channel_id = payload.get("channel_id")
    if schema_version == 2 and not _is_nonempty_str(channel_id):
        raise ValueError(f"{path} requires non-empty channel_id for schema_version=2.")

    packages_raw = payload.get("packages")
    if not isinstance(packages_raw, list):
        raise ValueError(f"{path} missing packages[] list.")

    package_ids: set[str] = set()
    for index, raw in enumerate(packages_raw, start=1):
        context = f"{path.name}.packages[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{context} must be an object.")

        package_id = str(raw.get("package_id") or "").strip().lower()
        title = str(raw.get("title") or "").strip()
        if not package_id or not title:
            raise ValueError(f"{context} missing package_id/title.")
        if package_id in package_ids:
            raise ValueError(f"{context} duplicate package_id={package_id!r}.")
        package_ids.add(package_id)

        zip_url = str(raw.get("zip_url") or "").strip()
        versions_raw = raw.get("versions")
        default_version = str(raw.get("default_version") or "").strip() or "branch-head"

        if schema_version == 2:
            if not _is_nonempty_str(raw.get("description")):
                raise ValueError(f"{context}.description is required in v2.")
            if not _is_nonempty_str(raw.get("upstream_repo_url")):
                raise ValueError(f"{context}.upstream_repo_url is required in v2.")
            _validate_term_list(raw.get("tags"), field_name="tags", context=context)

        version_ids: set[str] = set()
        if versions_raw is not None:
            if not isinstance(versions_raw, list) or not versions_raw:
                raise ValueError(f"{context}.versions must be a non-empty list when provided.")
            for version_index, version_raw in enumerate(versions_raw, start=1):
                version_context = f"{context}.versions[{version_index}]"
                if not isinstance(version_raw, dict):
                    raise ValueError(f"{version_context} must be an object.")

                version_id = str(version_raw.get("version_id") or "").strip()
                source_archive_url = str(version_raw.get("source_archive_url") or "").strip()
                if not version_id or not source_archive_url:
                    raise ValueError(
                        f"{version_context} missing version_id/source_archive_url."
                    )
                if version_id in version_ids:
                    raise ValueError(
                        f"{context}.versions has duplicate version_id={version_id!r}."
                    )
                version_ids.add(version_id)

                if not isinstance(version_raw.get("verified"), bool):
                    raise ValueError(f"{version_context}.verified must be boolean.")

                source_ref = version_raw.get("source_ref")
                if source_ref is not None:
                    if not isinstance(source_ref, dict):
                        raise ValueError(f"{version_context}.source_ref must be an object.")
                    source_ref_type = str(source_ref.get("type") or "").strip()
                    source_ref_value = str(source_ref.get("value") or "").strip()
                    if not source_ref_type or not source_ref_value:
                        raise ValueError(
                            f"{version_context}.source_ref requires type/value."
                        )

        if versions_raw is None and not zip_url:
            raise ValueError(
                f"{context} requires either zip_url or versions[] with source_archive_url."
            )
        if versions_raw is not None and default_version not in version_ids:
            raise ValueError(
                f"{context}.default_version={default_version!r} not in versions[]."
            )

    return sorted(package_ids), package_ids


def validate_family_hints(path: Path) -> tuple[list[str], set[str]]:
    payload = _load_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must be a JSON object.")

    schema_version = payload.get("schema_version", payload.get("version", 1))
    if not isinstance(schema_version, int):
        raise ValueError(f"{path} schema_version/version must be an integer.")
    if schema_version not in {1, 2}:
        raise ValueError(f"{path} schema_version {schema_version} is unsupported.")

    families_raw = payload.get("families")
    if not isinstance(families_raw, dict):
        raise ValueError(f"{path} missing families{{}} map.")

    family_ids: set[str] = set()
    for family_id_raw, family_payload in families_raw.items():
        family_id = str(family_id_raw).strip().lower()
        if not family_id:
            raise ValueError(f"{path} contains an empty family key.")
        if family_id in family_ids:
            raise ValueError(f"{path} duplicate family key {family_id!r}.")
        family_ids.add(family_id)

        context = f"{path.name}.families[{family_id}]"
        if not isinstance(family_payload, dict):
            raise ValueError(f"{context} must be an object.")

        if schema_version == 2 and not _is_nonempty_str(family_payload.get("description")):
            raise ValueError(f"{context}.description is required in v2.")

        _validate_term_list(
            family_payload.get("strong_keywords"),
            field_name="strong_keywords",
            context=context,
        )
        _validate_term_list(
            family_payload.get("keywords"),
            field_name="keywords",
            context=context,
        )
        _validate_term_list(
            family_payload.get("negative_keywords"),
            field_name="negative_keywords",
            context=context,
        )
        _validate_term_list(
            family_payload.get("package_id_overrides"),
            field_name="package_id_overrides",
            context=context,
        )

    return sorted(family_ids), family_ids


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_data.py",
        description="Validate curated channel and router family metadata JSON files.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root path (default: detected from script location).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve()
    data_root = repo_root / "src" / "fermilink" / "data"
    curated_dir = data_root / "curated_channels"
    family_hints_path = data_root / "router" / "family_hints.json"

    errors: list[str] = []
    curated_package_ids: set[str] = set()

    if not curated_dir.exists():
        errors.append(f"Missing curated channels directory: {curated_dir}")
    else:
        curated_files = sorted(curated_dir.glob("*.json"))
        if not curated_files:
            errors.append(f"No curated channel JSON files found in {curated_dir}")
        for curated_file in curated_files:
            try:
                _, package_ids = validate_curated_channel(curated_file)
                curated_package_ids.update(package_ids)
            except ValueError as exc:
                errors.append(str(exc))

    family_ids: set[str] = set()
    if not family_hints_path.exists():
        errors.append(f"Missing family hints file: {family_hints_path}")
    else:
        try:
            _, family_ids = validate_family_hints(family_hints_path)
        except ValueError as exc:
            errors.append(str(exc))

    if curated_package_ids and family_ids:
        missing_family_entries = sorted(curated_package_ids - family_ids)
        if missing_family_entries:
            errors.append(
                "Missing router family entries for curated packages: "
                + ", ".join(missing_family_entries)
            )

    if errors:
        for error in errors:
            print(f"[error] {error}", file=sys.stderr)
        return 1

    print("[ok] Data validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
