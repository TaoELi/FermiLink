"""Derivation loop support for FermiLink."""

from __future__ import annotations

from typing import Any


__all__ = ["DrvloopConfig", "run_drvloop"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from fermilink.drvloop.main import DrvloopConfig, run_drvloop

        exports = {
            "DrvloopConfig": DrvloopConfig,
            "run_drvloop": run_drvloop,
        }
        return exports[name]
    raise AttributeError(name)
