from __future__ import annotations

from importlib import resources


DRVLOOP_DONE_TOKEN = "<promise>DONE</promise>"
DRVLOOP_MEMORY_DIRNAME = "projects"
DRVLOOP_MEMORY_FILENAME = "memory.md"
DRVLOOP_STATE_DIRNAME = "fermilink-drvloop"
DRVLOOP_LEGACY_STATE_DIRNAME = ".fermilink-drvloop"
DRVLOOP_STATE_FILENAME = "state.json"
DRVLOOP_SPEC_FILENAME = "derivation_spec.yaml"
DRVLOOP_OBLIGATIONS_FILENAME = "proof_obligations.yaml"
DRVLOOP_VALIDATION_REPORT_FILENAME = "validation_report.json"
DRVLOOP_GOAL_CACHE_FILENAME = "goal_cache.json"
DRVLOOP_SKETCHES_FILENAME = "sketches.jsonl"
DRVLOOP_WORKFLOW_FILENAME = "workflow_state.json"
DRVLOOP_PUBLICATION_SWEEP_FILENAME = "publication_sweep.json"


DRVLOOP_PROMPT_PREFIX = (
    "FermiLink drvloop mode: derivation work.\n"
    "Read `AGENTS.md` and `projects/memory.md` before acting.\n"
    "You are a top-tier analytical specialist for derivations. Your task is to "
    "creatively and rigorously analyze the provided user prompt, then "
    "iteratively derive a solution in this loop mode.\n"
    "Treat `projects/*/derivation_spec.yaml` as the locked problem statement, "
    "record checkable claims in `proof_obligations.yaml`, and use validator "
    "and reviewer feedback to close failed or open proof obligations. Follow "
    "the workflow state before attempting finalization; publication-depth runs "
    "must populate and develop multiple routes before synthesis and final "
    "review. A separate post-ready publication sweep handles APS-style "
    "LaTeX/PDF export after the derivation workflow is final-ready.\n"
    f"Output `{DRVLOOP_DONE_TOKEN}` on its own line only when the requested "
    "derivation is complete, double checked, and both the validation report "
    "and workflow state are final-ready.\n"
)


def load_drvloop_guide() -> str:
    """Load the packaged drvloop AGENTS guide."""

    try:
        guide = resources.files("fermilink.drvloop").joinpath("AGENTS.md")
        return guide.read_text(encoding="utf-8")
    except Exception:
        return ""
