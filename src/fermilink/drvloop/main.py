from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from dataclasses import dataclass
from pathlib import Path
import shutil
import sys

from fermilink.drvloop.artifacts import record_artifact_changes
from fermilink.drvloop.instructions import materialize_drvloop_instructions
from fermilink.drvloop.memory import ensure_drvloop_memory
from fermilink.drvloop.prompts import (
    DRVLOOP_DONE_TOKEN,
    DRVLOOP_PROMPT_PREFIX,
    DRVLOOP_PUBLICATION_SWEEP_FILENAME,
    DRVLOOP_STATE_DIRNAME,
)
from fermilink.drvloop.sketches import (
    format_sketch_population,
    update_sketch_population,
)
from fermilink.drvloop.spec import (
    DerivationSpecContext,
    ensure_derivation_spec,
    format_spec_context,
)
from fermilink.drvloop.validation import (
    format_validation_feedback,
    run_drvloop_validation,
)
from fermilink.drvloop.workflow import (
    SUPPORTED_PROOF_DEPTHS,
    apply_workflow_gate_to_validation_report,
    evaluate_drvloop_workflow,
    format_workflow_feedback,
    normalize_proof_depth,
)


@dataclass(frozen=True)
class DrvloopConfig:
    repo_dir: Path
    user_prompt: str
    prompt_file: str | None = None
    max_iterations: int = 30
    sandbox: str | None = None
    proof_depth: str = "publication"


def run_drvloop(config: DrvloopConfig) -> int:
    """Run the minimal derivation loop."""

    repo_dir = config.repo_dir.resolve()
    repo_dir.mkdir(parents=True, exist_ok=True)
    try:
        instruction_files = materialize_drvloop_instructions(repo_dir)
    except OSError as exc:
        raise ValueError(
            f"Failed to prepare drvloop agent instructions: {exc}"
        ) from exc
    _print_tagged(
        "drvloop",
        f"instructions: {instruction_files.agents_path.relative_to(repo_dir)}",
    )
    memory_path = ensure_drvloop_memory(
        repo_dir=repo_dir,
        user_prompt=config.user_prompt,
        prompt_file=config.prompt_file,
    )
    _print_tagged("drvloop", f"memory: {memory_path.relative_to(repo_dir)}")
    spec_context = ensure_derivation_spec(
        repo_dir=repo_dir,
        user_prompt=config.user_prompt,
        prompt_file=config.prompt_file,
    )
    _print_tagged("drvloop", f"spec: {spec_context.spec_rel}")

    max_iterations = _positive_int(config.max_iterations, "max_iterations")
    proof_depth = normalize_proof_depth(config.proof_depth)
    _print_tagged("drvloop", f"proof depth: {proof_depth}")

    for iteration in range(1, max_iterations + 1):
        _print_tagged("drvloop", f"iteration {iteration}/{max_iterations}")
        spec_context = ensure_derivation_spec(
            repo_dir=repo_dir,
            user_prompt=config.user_prompt,
            prompt_file=config.prompt_file,
        )
        artifact_changes = record_artifact_changes(repo_dir, memory_path)
        validation_report = run_drvloop_validation(
            repo_dir=repo_dir,
            spec_context=spec_context,
        )
        workflow_state = evaluate_drvloop_workflow(
            repo_dir=repo_dir,
            spec_context=spec_context,
            validation_report=validation_report,
            proof_depth=proof_depth,
            iteration=iteration,
        )
        validation_report = apply_workflow_gate_to_validation_report(
            repo_dir=repo_dir,
            validation_report=validation_report,
            workflow_state=workflow_state,
        )
        sketch_records = update_sketch_population(
            repo_dir=repo_dir,
            spec_context=spec_context,
            validation_report=validation_report,
            artifact_changes=artifact_changes,
        )
        prompt = build_drvloop_prompt(
            repo_dir=repo_dir,
            user_prompt=config.user_prompt,
            artifact_changes=artifact_changes,
            spec_context=spec_context,
            validation_report=validation_report,
            sketch_records=sketch_records,
            workflow_state=workflow_state,
        )
        run_result = _run_provider_turn(
            repo_dir=repo_dir,
            prompt=prompt,
            sandbox=config.sandbox,
        )
        if bool(run_result.get("stopped_by_user")):
            _print_tagged("drvloop", "provider run stopped by user")
            return 130

        assistant_text = str(run_result.get("assistant_text") or "")
        if any(
            line.strip() == DRVLOOP_DONE_TOKEN for line in assistant_text.splitlines()
        ):
            post_changes = record_artifact_changes(repo_dir, memory_path)
            spec_context = ensure_derivation_spec(
                repo_dir=repo_dir,
                user_prompt=config.user_prompt,
                prompt_file=config.prompt_file,
            )
            final_report = run_drvloop_validation(
                repo_dir=repo_dir,
                spec_context=spec_context,
            )
            final_workflow = evaluate_drvloop_workflow(
                repo_dir=repo_dir,
                spec_context=spec_context,
                validation_report=final_report,
                proof_depth=proof_depth,
                iteration=iteration,
            )
            final_report = apply_workflow_gate_to_validation_report(
                repo_dir=repo_dir,
                validation_report=final_report,
                workflow_state=final_workflow,
            )
            update_sketch_population(
                repo_dir=repo_dir,
                spec_context=spec_context,
                validation_report=final_report,
                artifact_changes=post_changes,
            )
            if bool(final_report.get("final_ready")):
                if proof_depth == "publication":
                    sweep_status = _publication_sweep_status(
                        repo_dir=repo_dir,
                        spec_context=spec_context,
                    )
                    if not bool(sweep_status.get("complete")):
                        _print_tagged(
                            "drvloop",
                            (
                                "derivation is final-ready; running final "
                                "publication export sweep"
                            ),
                        )
                        _save_publication_sweep_status(
                            repo_dir,
                            {
                                **sweep_status,
                                "attempted": True,
                                "status": "running",
                                "started_at_utc": _utc_now_z(),
                            },
                        )
                        sweep_prompt = build_publication_sweep_prompt(
                            repo_dir=repo_dir,
                            user_prompt=config.user_prompt,
                            spec_context=spec_context,
                            validation_report=final_report,
                            workflow_state=final_workflow,
                            sweep_status=sweep_status,
                        )
                        sweep_result = _run_provider_turn(
                            repo_dir=repo_dir,
                            prompt=sweep_prompt,
                            sandbox=config.sandbox,
                        )
                        if bool(sweep_result.get("stopped_by_user")):
                            _print_tagged(
                                "drvloop", "publication sweep stopped by user"
                            )
                            return 130
                        sweep_return_code = int(sweep_result.get("return_code") or 0)
                        if sweep_return_code != 0:
                            stderr = str(sweep_result.get("stderr") or "").strip()
                            if stderr:
                                print(stderr)
                            _print_tagged(
                                "drvloop",
                                (
                                    "publication sweep provider exited with code "
                                    f"{sweep_return_code}"
                                ),
                            )
                            return sweep_return_code
                        post_changes = record_artifact_changes(repo_dir, memory_path)
                        final_report = run_drvloop_validation(
                            repo_dir=repo_dir,
                            spec_context=spec_context,
                        )
                        final_workflow = evaluate_drvloop_workflow(
                            repo_dir=repo_dir,
                            spec_context=spec_context,
                            validation_report=final_report,
                            proof_depth=proof_depth,
                            iteration=iteration,
                        )
                        final_report = apply_workflow_gate_to_validation_report(
                            repo_dir=repo_dir,
                            validation_report=final_report,
                            workflow_state=final_workflow,
                        )
                        update_sketch_population(
                            repo_dir=repo_dir,
                            spec_context=spec_context,
                            validation_report=final_report,
                            artifact_changes=post_changes,
                        )
                        sweep_status = _publication_sweep_status(
                            repo_dir=repo_dir,
                            spec_context=spec_context,
                        )
                        _save_publication_sweep_status(repo_dir, sweep_status)
                        if not bool(final_report.get("final_ready")) or not bool(
                            sweep_status.get("complete")
                        ):
                            _print_tagged(
                                "drvloop",
                                (
                                    "DONE withheld because final publication "
                                    "sweep is not complete; continuing"
                                ),
                            )
                            if iteration >= max_iterations:
                                break
                            continue
                print(DRVLOOP_DONE_TOKEN)
                return 0
            _print_tagged(
                "drvloop",
                (
                    "DONE withheld because validation/workflow is not "
                    "final-ready; continuing"
                ),
            )
            if iteration >= max_iterations:
                break
            continue

        return_code = int(run_result.get("return_code") or 0)
        if return_code != 0:
            stderr = str(run_result.get("stderr") or "").strip()
            if stderr:
                print(stderr)
            _print_tagged("drvloop", f"provider exited with code {return_code}")
            return return_code

        if iteration >= max_iterations:
            break

    _print_tagged("drvloop", "max iterations reached before DONE")
    return 1


def build_drvloop_prompt(
    *,
    repo_dir: Path,
    user_prompt: str,
    artifact_changes: list[dict[str, object]],
    spec_context: DerivationSpecContext,
    validation_report: dict[str, object],
    sketch_records: list[dict[str, object]],
    workflow_state: dict[str, object],
) -> str:
    skill_lines = _discover_skill_lines(repo_dir)
    artifact_lines = _format_artifact_change_lines(artifact_changes)

    parts = [DRVLOOP_PROMPT_PREFIX.rstrip()]
    parts.append("Local derivation skills:\n" + "\n".join(skill_lines))
    parts.append("Locked derivation spec:\n" + format_spec_context(spec_context))
    parts.append(
        "New or modified derivation artifacts before this turn:\n"
        + "\n".join(artifact_lines)
    )
    parts.append(
        "Validation report before this turn:\n"
        + format_validation_feedback(validation_report)
    )
    parts.append(
        "Workflow state before this turn:\n" + format_workflow_feedback(workflow_state)
    )
    parts.append(
        "Proof-sketch population before this turn:\n"
        + format_sketch_population(sketch_records)
    )
    parts.append("Request:\n" + user_prompt.strip())
    return "\n\n".join(parts).rstrip() + "\n"


def build_publication_sweep_prompt(
    *,
    repo_dir: Path,
    user_prompt: str,
    spec_context: DerivationSpecContext,
    validation_report: dict[str, object],
    workflow_state: dict[str, object],
    sweep_status: dict[str, object],
) -> str:
    project_path = repo_dir / spec_context.project_rel
    source_lines = _format_publication_source_lines(repo_dir, project_path)
    pdflatex_available = shutil.which("pdflatex") is not None
    latexmk_available = shutil.which("latexmk") is not None
    compiler_line = (
        f"- pdflatex_available: {pdflatex_available}\n"
        f"- latexmk_available: {latexmk_available}"
    )
    required_pdf = "yes" if pdflatex_available else "no"
    status_text = json.dumps(sweep_status, indent=2, sort_keys=True)
    return (
        "FermiLink drvloop final publication sweep.\n"
        "The analytical derivation workflow is already final-ready. This is a "
        "fresh final round for publication presentation, not another derivation "
        "route. Read `AGENTS.md`, `projects/memory.md`, the locked derivation "
        "spec, the final manuscript, the pedagogical note, validation report, "
        "and workflow state before writing.\n\n"
        "Rules:\n"
        "- Do not weaken or amend `derivation_spec.yaml`.\n"
        "- Do not reopen the analytical derivation unless needed to fix a real "
        "LaTeX build error or presentation inconsistency.\n"
        "- Do not mechanically convert Markdown with Pandoc as the final result; "
        "rewrite and organize the material as a polished APS-style preprint.\n"
        "- Keep the equations and claims consistent with the validated "
        "derivation artifacts.\n"
        "- Add only presentation/build obligations unless you discover a genuine "
        "analytical problem.\n\n"
        "Required outputs under the active project:\n"
        f"- `{spec_context.project_rel}/final_manuscript_aps.tex`: a "
        "publication-ready APS/RevTeX-style preprint manuscript. Use RevTeX "
        "if available; otherwise use a portable article fallback with clear APS "
        "preprint structure. Include abstract, introduction, theory/model, "
        "realistic lineshape velocity analysis, ballistic-to-diffusive turnover, "
        "numerical evidence, limitations, and conclusion.\n"
        f"- `{spec_context.project_rel}/pedagogical_note_grad.tex`: a "
        "companion note for entry-level graduate students with slower "
        "derivations, definitions, and physical intuition.\n"
        f"- PDF builds for both files: required={required_pdf}. If `pdflatex` "
        "is available, compile to `final_manuscript_aps.pdf` and "
        "`pedagogical_note_grad.pdf` using `latexmk` or `pdflatex`.\n"
        f"- `{spec_context.project_rel}/20_publication_sweep.md`: record the "
        "style choices, source artifacts used, build commands, compiler "
        "availability, and build results.\n"
        "- Update `proof_obligations.yaml` with `latex_build` obligations that "
        "use the field `path:` for each TeX file, `compile: true`, and build "
        "evidence. Do not use `latex_path:` as the primary path field.\n\n"
        "Compiler availability:\n"
        f"{compiler_line}\n\n"
        "Locked derivation spec:\n"
        f"{format_spec_context(spec_context)}\n\n"
        "Validation report before publication sweep:\n"
        f"{format_validation_feedback(validation_report)}\n\n"
        "Workflow state before publication sweep:\n"
        f"{format_workflow_feedback(workflow_state)}\n\n"
        "Publication sweep status before this turn:\n"
        f"{status_text}\n\n"
        "Likely source artifacts:\n" + "\n".join(source_lines) + "\n\n"
        "Original request:\n"
        f"{user_prompt.strip()}\n\n"
        f"Output `{DRVLOOP_DONE_TOKEN}` on its own line only after the required "
        "TeX files are present and, when required, both PDFs compile."
    )


def publication_sweep_path_for(repo_dir: Path) -> Path:
    return repo_dir / DRVLOOP_STATE_DIRNAME / DRVLOOP_PUBLICATION_SWEEP_FILENAME


def _publication_sweep_status(
    *,
    repo_dir: Path,
    spec_context: DerivationSpecContext,
) -> dict[str, object]:
    state = _load_publication_sweep_status(repo_dir)
    project_path = repo_dir / spec_context.project_rel
    manuscript_tex = project_path / "final_manuscript_aps.tex"
    note_tex = project_path / "pedagogical_note_grad.tex"
    manuscript_pdf = project_path / "final_manuscript_aps.pdf"
    note_pdf = project_path / "pedagogical_note_grad.pdf"
    pdflatex_available = shutil.which("pdflatex") is not None
    tex_ready = manuscript_tex.is_file() and note_tex.is_file()
    pdf_ready = manuscript_pdf.is_file() and note_pdf.is_file()
    complete = (
        bool(state.get("attempted"))
        and tex_ready
        and (not pdflatex_available or pdf_ready)
    )
    status = {
        **state,
        "schema_version": 1,
        "updated_at_utc": _utc_now_z(),
        "project": spec_context.project_rel,
        "attempted": bool(state.get("attempted")),
        "complete": complete,
        "status": "complete" if complete else str(state.get("status") or "pending"),
        "pdflatex_available": pdflatex_available,
        "required": {
            "manuscript_tex": (f"{spec_context.project_rel}/final_manuscript_aps.tex"),
            "pedagogical_note_tex": (
                f"{spec_context.project_rel}/pedagogical_note_grad.tex"
            ),
            "pdfs_required": pdflatex_available,
        },
        "artifacts": {
            "manuscript_tex": _rel_if_exists(repo_dir, manuscript_tex),
            "pedagogical_note_tex": _rel_if_exists(repo_dir, note_tex),
            "manuscript_pdf": _rel_if_exists(repo_dir, manuscript_pdf),
            "pedagogical_note_pdf": _rel_if_exists(repo_dir, note_pdf),
            "record": _rel_if_exists(
                repo_dir,
                project_path / "20_publication_sweep.md",
            ),
        },
    }
    if complete and "completed_at_utc" not in status:
        status["completed_at_utc"] = status["updated_at_utc"]
    return status


def _format_publication_source_lines(repo_dir: Path, project_path: Path) -> list[str]:
    if not project_path.is_dir():
        return ["- Active project directory does not exist yet."]
    patterns = [
        "*manuscript*.md",
        "final*.md",
        "*pedagogical*.md",
        "*note*.md",
        "*synthesis*.md",
        "*review*.md",
        "*numerical*.md",
        "*.json",
    ]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(project_path.glob(pattern))
    unique = sorted({path for path in paths if path.is_file()})
    if not unique:
        return ["- No source artifacts found yet."]
    lines: list[str] = []
    for path in unique[:30]:
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            rel = path.as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        lines.append(f"- {rel} ({size} bytes)")
    if len(unique) > 30:
        lines.append(f"- ... {len(unique) - 30} additional source artifact(s)")
    return lines


def _load_publication_sweep_status(repo_dir: Path) -> dict[str, object]:
    path = publication_sweep_path_for(repo_dir)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_publication_sweep_status(repo_dir: Path, status: dict[str, object]) -> None:
    path = publication_sweep_path_for(repo_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _rel_if_exists(base_dir: Path, path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        return path.relative_to(base_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _utc_now_z() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _discover_skill_lines(repo_dir: Path) -> list[str]:
    skills_root = repo_dir / "skills"
    if not skills_root.is_dir():
        return ["- No local `skills/` directory was found."]
    skill_paths = sorted(skills_root.rglob("SKILL.md"))
    if not skill_paths:
        return ["- `skills/` exists, but no `SKILL.md` files were found."]
    lines: list[str] = []
    for path in skill_paths[:50]:
        try:
            rel = path.relative_to(repo_dir).as_posix()
        except ValueError:
            rel = str(path)
        lines.append(f"- {rel}")
    if len(skill_paths) > 50:
        lines.append(f"- ... {len(skill_paths) - 50} additional skill file(s)")
    return lines


def _format_artifact_change_lines(
    artifact_changes: list[dict[str, object]],
) -> list[str]:
    if not artifact_changes:
        return ["- No new or modified derivation artifacts were detected."]
    lines: list[str] = []
    for item in artifact_changes[:50]:
        path = str(item.get("path") or "")
        status = str(item.get("status") or "changed")
        size = item.get("size_bytes")
        modified = str(item.get("modified_utc") or "")
        lines.append(f"- {path} | {status} | {size} bytes | modified {modified}")
    if len(artifact_changes) > 50:
        lines.append(f"- ... {len(artifact_changes) - 50} additional artifact(s)")
    return lines


def _run_provider_turn(
    *,
    repo_dir: Path,
    prompt: str,
    sandbox: str | None,
) -> dict[str, object]:
    from fermilink import cli

    runtime_policy = cli.resolve_agent_runtime_policy()
    sandbox_policy = runtime_policy.sandbox_policy
    sandbox_mode = runtime_policy.sandbox_mode
    if isinstance(sandbox, str) and sandbox.strip():
        sandbox_policy = "enforce"
        sandbox_mode = sandbox.strip()
    provider_bin = cli.resolve_provider_binary_override(
        runtime_policy.provider,
        raw_override=cli.DEFAULT_PROVIDER_BINARY_OVERRIDE,
    )
    return cli._run_exec_chat_turn(
        repo_dir=repo_dir,
        prompt=prompt,
        sandbox=sandbox_mode if sandbox_policy == "enforce" else None,
        provider_bin_override=provider_bin,
        provider=runtime_policy.provider,
        sandbox_policy=sandbox_policy,
        model=runtime_policy.model,
        reasoning_effort=runtime_policy.reasoning_effort,
    )


def _resolve_prompt(tokens: list[str], repo_dir: Path) -> tuple[str, str | None]:
    if len(tokens) == 1:
        candidate = Path(tokens[0]).expanduser()
        if not candidate.is_absolute():
            candidate = (repo_dir / candidate).resolve()
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8", errors="replace").strip()
            if not text:
                raise ValueError(f"Prompt file is empty: {candidate}")
            return text, str(candidate)
    text = " ".join(tokens).strip()
    if not text:
        raise ValueError("Prompt is required.")
    return text, None


def _positive_int(value: int, name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be >= 1.")
    return parsed


def _print_tagged(tag: str, message: str, *, stderr: bool = False) -> None:
    kwargs: dict[str, object] = {"flush": True}
    if stderr:
        kwargs["file"] = sys.stderr
    print(f"[{tag}] {message}", **kwargs)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m fermilink.drvloop.main",
        description=(
            "Debug runner for the derivation loop. "
            "The same implementation is used by top-level `fermilink drvloop`."
        ),
    )
    parser.add_argument(
        "prompt",
        nargs="+",
        help="Prompt text or a markdown/text goal file path.",
    )
    parser.add_argument("--max-iterations", type=int, default=30)
    parser.add_argument("--sandbox", default=None)
    parser.add_argument(
        "--proof-depth",
        choices=SUPPORTED_PROOF_DEPTHS,
        default="publication",
        help=(
            "Derivation workflow rigor: quick is validator-only, standard "
            "requires a staged derivation, publication requires multi-route "
            "population and review (default: publication)."
        ),
    )
    return parser


def cmd_drvloop(args: argparse.Namespace) -> int:
    repo_dir = Path.cwd().resolve()
    user_prompt, prompt_file = _resolve_prompt(args.prompt, repo_dir)
    config = DrvloopConfig(
        repo_dir=repo_dir,
        user_prompt=user_prompt,
        prompt_file=prompt_file,
        max_iterations=args.max_iterations,
        sandbox=args.sandbox,
        proof_depth=getattr(args, "proof_depth", "publication"),
    )
    return run_drvloop(config)


def main(argv: list[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    return cmd_drvloop(args)


if __name__ == "__main__":
    raise SystemExit(main())
