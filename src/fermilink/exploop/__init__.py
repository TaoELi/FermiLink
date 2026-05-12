"""Experimental measurement loop support for FermiLink."""

from __future__ import annotations

from typing import Any


__all__ = ["ExploopConfig", "run_exploop"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from fermilink.exploop.main import ExploopConfig, run_exploop

        exports = {
            "ExploopConfig": ExploopConfig,
            "run_exploop": run_exploop,
        }
        return exports[name]
    raise AttributeError(name)
