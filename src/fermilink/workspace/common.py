from __future__ import annotations

import importlib
from importlib import resources
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


def resolve_exploop_agents_source() -> Path:
    try:
        candidate = resources.files("fermilink.exploop").joinpath("AGENTS.md")
        agents_source = Path(str(candidate))
    except Exception:
        agents_source = None

    if agents_source is not None and agents_source.is_file():
        return agents_source

    exploop_module = importlib.import_module("fermilink.exploop")
    module_file = getattr(exploop_module, "__file__", None)
    if isinstance(module_file, str) and module_file:
        fallback = Path(module_file).resolve().parent / "AGENTS.md"
        if fallback.is_file():
            return fallback

    raise FileNotFoundError("Missing exploop AGENTS.md template.")
