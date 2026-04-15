from __future__ import annotations

import argparse
import json
from pathlib import Path

from fermilink.config import resolve_fermilink_home
from fermilink.workspace.common import _cli, resolve_cli_path


HPC_PROFILE_FILENAME = "HPC_PROFILE.json"
LEGACY_HPC_PROFILE_FILENAME = "hpc_profile.json"
HPC_PROFILE_REQUIRED_KEYS = (
    "slurm_default_partition",
    "slurm_defaults",
    "slurm_resource_policy",
)
DEFAULT_HPC_PROFILE_PAYLOAD = {
    "slurm_default_partition": "shared",
    "slurm_defaults": (
        "--nodes=1 --ntasks=1 --ntasks-per-node=1 " "--cpus-per-task=1 --time=24:00:00"
    ),
    "slurm_resource_policy": (
        "Use serial/single-node defaults unless the method explicitly "
        "requires MPI or multi-node scaling"
    ),
}


def normalize_hpc_profile_payload(
    raw_profile: dict[str, object], *, profile_label: str
) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key in HPC_PROFILE_REQUIRED_KEYS:
        raw_value = raw_profile.get(key)
        if raw_value is None:
            raise ValueError(
                f"HPC profile missing required `{key}` in {profile_label}."
            )
        if not isinstance(raw_value, str):
            raise ValueError(
                f"HPC profile `{key}` must be a non-empty string in {profile_label}."
            )
        text_value = " ".join(raw_value.strip().split())
        if not text_value:
            raise ValueError(
                f"HPC profile `{key}` must be a non-empty string in {profile_label}."
            )
        normalized[key] = text_value
    return normalized


def load_hpc_profile_payload(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"HPC profile does not exist: {path}")
    if not path.is_file():
        raise ValueError(f"HPC profile must be a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Failed to read HPC profile: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"HPC profile must contain valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"HPC profile root JSON must be an object: {path}")
    return normalize_hpc_profile_payload(payload, profile_label=str(path))


def default_hpc_profile_path() -> Path:
    return resolve_fermilink_home() / HPC_PROFILE_FILENAME


def legacy_hpc_profile_path() -> Path:
    return resolve_fermilink_home() / LEGACY_HPC_PROFILE_FILENAME


def ensure_default_hpc_profile() -> tuple[Path, bool, bool]:
    destination = default_hpc_profile_path()
    if destination.is_file():
        return destination, False, False

    legacy = legacy_hpc_profile_path()
    if legacy.is_file():
        normalized = load_hpc_profile_payload(legacy)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(normalized, indent=2) + "\n", encoding="utf-8"
        )
        return destination, True, True

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(DEFAULT_HPC_PROFILE_PAYLOAD, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination, True, False


def set_hpc_profile(source_file: Path, destination_file: Path | None = None) -> Path:
    normalized = load_hpc_profile_payload(source_file)
    destination = destination_file or default_hpc_profile_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return destination


def cmd_hpc(args: argparse.Namespace) -> int:
    cli = _cli()
    hpc_command = str(getattr(args, "hpc_command", "") or "").strip().lower()
    try:
        if not hpc_command:
            _, created, migrated = ensure_default_hpc_profile()
            home = resolve_fermilink_home()
            canonical_path = home / HPC_PROFILE_FILENAME
            if created:
                if migrated:
                    print(
                        f"[fermilink-hpc] Migrated legacy {LEGACY_HPC_PROFILE_FILENAME} "
                        f"to {canonical_path}"
                    )
                else:
                    print(
                        "[fermilink-hpc] Created default HPC profile at "
                        f"{canonical_path}"
                    )
            else:
                print(
                    f"[fermilink-hpc] {canonical_path} already exists. "
                    "No copy was made."
                )
            print(
                "[fermilink-hpc] You can adjust the profile as needed for your "
                "HPC environment."
            )
            return 0

        if hpc_command == "set":
            raw_source = str(getattr(args, "file", "") or "").strip()
            if not raw_source:
                raise ValueError("Please provide a JSON profile path.")
            source = resolve_cli_path(raw_source)
            destination = set_hpc_profile(source)
            print(f"[fermilink-hpc] Installed profile at {destination}")
            return 0
    except Exception as exc:
        raise cli.PackageError(str(exc)) from exc

    raise cli.PackageError(f"Unknown hpc command: {hpc_command}")
