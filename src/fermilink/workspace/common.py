from __future__ import annotations

import importlib
from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def resolve_cli_path(raw_value: object, *, default: str = ".") -> Path:
    raw_text = str(raw_value or default).strip() or default
    candidate = Path(raw_text).expanduser()
    if not candidate.is_absolute():
        return (Path.cwd() / candidate).resolve()
    return candidate.resolve()


def load_runner_app_module():
    return importlib.import_module("fermilink.runner.app")


def load_runner_scipkg_module():
    return importlib.import_module("fermilink.runner.scientific_packages")


def resolve_software_agents_source() -> Path:
    runner_app = load_runner_app_module()
    source_dir = runner_app._resolve_source_dir()
    agents_source = runner_app._resolve_template_agents_path(source_dir)
    if agents_source is None or not agents_source.is_file():
        raise FileNotFoundError(
            f"Missing software AGENTS.md template under source root {source_dir}"
        )
    return agents_source
