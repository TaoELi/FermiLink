from __future__ import annotations

import functools


def _cli():
    from fermilink import cli

    return cli


@functools.lru_cache(maxsize=1)
def _load_web_router_module():
    cli = _cli()
    # CLI exec/chat only need routing helpers; avoid web-only filesystem setup.
    cli.os.environ.setdefault(cli.WEB_ROUTER_ONLY_IMPORT_ENV, "1")
    # Some HPC systems export PROJECT as a filesystem path (e.g. /anvil/projects/...).
    # Chainlit parses PROJECT for a structured settings field named "project",
    # which can raise pydantic SettingsError during import when the value is not JSON.
    saved_project = cli.os.environ.pop("PROJECT", None)
    try:
        return cli.importlib.import_module("fermilink.web.app")
    finally:
        if saved_project is not None:
            cli.os.environ["PROJECT"] = saved_project


@functools.lru_cache(maxsize=1)
def _load_runner_app_module():
    from fermilink.runner import app as runner_app

    return runner_app
