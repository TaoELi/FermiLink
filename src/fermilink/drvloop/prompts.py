from __future__ import annotations

from importlib import resources


DRVLOOP_DONE_TOKEN = "<promise>DONE</promise>"
DRVLOOP_MEMORY_DIRNAME = "projects"
DRVLOOP_MEMORY_FILENAME = "memory.md"
DRVLOOP_STATE_DIRNAME = "fermilink-drvloop"
DRVLOOP_LEGACY_STATE_DIRNAME = ".fermilink-drvloop"
DRVLOOP_STATE_FILENAME = "state.json"


DRVLOOP_PROMPT_PREFIX = (
    "FermiLink drvloop mode: derivation work.\n"
    "Read `AGENTS.md` and `projects/memory.md` before acting.\n"
    "You are a top-tier analytical specialist for derivations. Your task is to "
    "creatively and rigorously analyze the provided user prompt, then "
    "iteratively derive a solution in this loop mode.\n"
    f"Output `{DRVLOOP_DONE_TOKEN}` on its own line only when the requested "
    "derivation is complete and double checked.\n"
)


def load_drvloop_guide() -> str:
    """Load the packaged drvloop AGENTS guide."""

    try:
        guide = resources.files("fermilink.drvloop").joinpath("AGENTS.md")
        return guide.read_text(encoding="utf-8")
    except Exception:
        return ""
