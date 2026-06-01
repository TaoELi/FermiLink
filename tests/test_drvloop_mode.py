from __future__ import annotations

import json
from pathlib import Path

from fermilink import cli
from fermilink.drvloop import artifacts
from fermilink.drvloop.instructions import materialize_drvloop_instructions
from fermilink.drvloop.main import DrvloopConfig, run_drvloop
from fermilink.drvloop import main as drvloop_main
from fermilink.drvloop.memory import ensure_drvloop_memory
from fermilink.drvloop.prompts import DRVLOOP_DONE_TOKEN, load_drvloop_guide
from fermilink.drvloop.spec import ensure_derivation_spec
from fermilink.drvloop.validation import collect_obligations, run_drvloop_validation
from fermilink.drvloop.workflow import (
    apply_workflow_gate_to_validation_report,
    evaluate_drvloop_workflow,
)


def _write_passing_target_obligation(repo: Path) -> Path:
    specs = sorted((repo / "projects").glob("*/derivation_spec.yaml"))
    assert specs
    project_dir = specs[-1].parent
    obligations = project_dir / "proof_obligations.yaml"
    obligations.write_text(
        "obligations:\n"
        "  - id: algebra-target\n"
        "    type: algebra\n"
        "    claim: target algebra identity\n"
        "    lhs: (x + 1)**2\n"
        "    rhs: x**2 + 2*x + 1\n"
        "    symbols: [x]\n"
        "    covers_target_claims: [target-1]\n"
        "    critical: true\n",
        encoding="utf-8",
    )
    _write_final_artifacts(project_dir)
    return obligations


def _write_final_artifacts(project_dir: Path) -> None:
    (project_dir / "final_derivation.md").write_text(
        "# Final derivation\nThe checked target derivation is complete.\n",
        encoding="utf-8",
    )
    (project_dir / "pedagogical_note.md").write_text(
        "# Pedagogical note\nThe derivation is explained step by step.\n",
        encoding="utf-8",
    )


def _write_publication_ready_artifacts(project_dir: Path) -> None:
    (project_dir / "00_pathways.md").write_text(
        "\n".join(f"Pathway {index}: route {index}" for index in range(1, 11)),
        encoding="utf-8",
    )
    for index in range(1, 11):
        (project_dir / f"{index:02d}_pathway{index}_route_detail.md").write_text(
            "\n".join([f"# Pathway {index}", "Detailed derivation line."] * 45),
            encoding="utf-8",
        )
    (project_dir / "01_route_ranking.md").write_text(
        "rank score selected route tradeoff\n", encoding="utf-8"
    )
    (project_dir / "11_official_derivation_synthesis.md").write_text(
        "synthesis\n", encoding="utf-8"
    )
    manuscript_lines = ["# Final manuscript"]
    manuscript_lines.extend(["regular derivation text"] * 500)
    manuscript_lines.extend([r"$$ x = x $$"] * 12)
    (project_dir / "12_final_manuscript.md").write_text(
        "\n".join(manuscript_lines), encoding="utf-8"
    )
    (project_dir / "13_manuscript_gap_review.md").write_text(
        "gap review remaining limitations checked\n", encoding="utf-8"
    )
    (project_dir / "numerical_checks.py").write_text("print('ok')\n", encoding="utf-8")
    (project_dir / "numerical_summary.json").write_text("{}\n", encoding="utf-8")
    (project_dir / "14_pedagogical_note.md").write_text(
        "\n".join(["pedagogical derivation"] * 130), encoding="utf-8"
    )
    (project_dir / "15_final_consistency_review.md").write_text(
        "final consistency review complete\n", encoding="utf-8"
    )
    (project_dir / "proof_obligations.yaml").write_text(
        "obligations:\n"
        + "\n".join(
            [
                f"  - id: algebra-target-{index}\n"
                f"    type: algebra\n"
                f"    source_file: projects/{project_dir.name}/12_final_manuscript.md\n"
                f"    lhs: x + {index}\n"
                f"    rhs: x + {index}\n"
                f"    symbols: [x]\n"
                f"    covers_target_claims: [target-1]\n"
                f"    critical: true\n"
                for index in range(1, 11)
            ]
        ),
        encoding="utf-8",
    )


def test_materialize_drvloop_instructions_copies_guide_and_aliases(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    instruction_files = materialize_drvloop_instructions(repo)

    agents_path = repo / "AGENTS.md"
    assert instruction_files.agents_path == agents_path
    assert agents_path.read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
        if alias_path.is_symlink():
            assert alias_path.resolve() == agents_path.resolve()
        else:
            assert alias_path.read_text(encoding="utf-8") == load_drvloop_guide()


def test_materialize_drvloop_instructions_preserves_differing_agents_backup(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("custom local guide\n", encoding="utf-8")

    materialize_drvloop_instructions(repo)

    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    backup = repo / "AGENTS.md.pre-drvloop-backup"
    assert backup.read_text(encoding="utf-8") == "custom local guide\n"


def test_drvloop_memory_template_is_compact_major_step_memory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive the coupled oscillator response",
        prompt_file=None,
    )

    memory_text = memory.read_text(encoding="utf-8")
    assert "### Major done" in memory_text
    assert "### Major needed" in memory_text
    assert "### Major conclusions" in memory_text
    assert "Measurement data inventory" not in memory_text
    assert "Experiment history" not in memory_text
    assert "Simulation history" not in memory_text


def test_drvloop_runs_without_git_init_and_uses_minimal_prompt(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    skill_dir = repo / "skills" / "symbolic-derivation"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Symbolic derivation\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        _write_passing_target_obligation(repo)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive the adiabatic elimination equations",
            max_iterations=1,
            proof_depth="quick",
        )
    )

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert (repo / "fermilink-drvloop" / "validation_report.json").is_file()
    assert (repo / "fermilink-drvloop" / "goal_cache.json").is_file()
    assert (repo / "fermilink-drvloop" / "sketches.jsonl").is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    memory = repo / "projects" / "memory.md"
    assert memory.is_file()
    memory_text = memory.read_text(encoding="utf-8")
    assert "derive the adiabatic elimination equations" in memory_text
    assert "Measurement data inventory" not in memory_text
    prompt = str(captured.get("prompt") or "")
    assert "FermiLink drvloop mode: derivation work." in prompt
    assert "Read `AGENTS.md` and `projects/memory.md`" in prompt
    assert "Locked derivation spec:" in prompt
    assert "Validation report before this turn:" in prompt
    assert "Workflow state before this turn:" in prompt
    assert "Proof-sketch population before this turn:" in prompt
    assert "skills/symbolic-derivation/SKILL.md" in prompt
    assert "<pid_number>" not in prompt
    assert "<wait_seconds>" not in prompt
    assert "# FermiLink Drvloop Guide" not in prompt


def test_drvloop_artifact_scan_records_state_without_expanding_memory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive",
        prompt_file=None,
    )

    first_changes = artifacts.record_artifact_changes(repo, memory)
    assert first_changes == []
    assert artifacts.state_path_for(repo) == repo / "fermilink-drvloop" / "state.json"
    assert artifacts.state_path_for(repo).is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()

    run_dir = repo / "projects" / "2026-05-24-derivation"
    run_dir.mkdir(parents=True)
    derivation_file = run_dir / "route-a.md"
    derivation_file.write_text("step 1\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)

    assert [item["path"] for item in changes] == [
        "projects/2026-05-24-derivation/route-a.md"
    ]
    memory_text = memory.read_text(encoding="utf-8")
    assert "projects/2026-05-24-derivation/route-a.md" not in memory_text
    assert "Measurement data inventory" not in memory_text


def test_drvloop_artifact_state_reads_legacy_hidden_state_then_writes_visible_state(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="derive",
        prompt_file=None,
    )
    run_dir = repo / "projects" / "2026-05-24-derivation"
    run_dir.mkdir(parents=True)
    old_file = run_dir / "old.md"
    old_file.write_text("old\n", encoding="utf-8")
    old_record = artifacts.snapshot_project_artifacts(repo)[
        "projects/2026-05-24-derivation/old.md"
    ]
    legacy_state = repo / ".fermilink-drvloop" / "state.json"
    legacy_state.parent.mkdir(parents=True)
    legacy_payload = {
        "version": 1,
        "last_scan_at_utc": "2026-05-24T00:00:00Z",
        "known_artifacts": {
            "projects/2026-05-24-derivation/old.md": old_record,
        },
    }
    legacy_state.write_text(json.dumps(legacy_payload), encoding="utf-8")

    new_file = run_dir / "new.md"
    new_file.write_text("new\n", encoding="utf-8")

    changes = artifacts.record_artifact_changes(repo, memory)

    assert [item["path"] for item in changes] == [
        "projects/2026-05-24-derivation/new.md"
    ]
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert legacy_state.is_file()


def test_drvloop_runs_next_turn_immediately_until_done(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    provider_calls: list[dict[str, object]] = []

    def fake_provider_turn(**kwargs):
        provider_calls.append(kwargs)
        if len(provider_calls) == 1:
            return {
                "assistant_text": "stored route A and will continue\n",
                "return_code": 0,
                "stderr": "",
            }
        _write_passing_target_obligation(repo)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive route A then check route B",
            max_iterations=2,
            proof_depth="quick",
        )
    )

    assert code == 0
    assert len(provider_calls) == 2


def test_drvloop_done_is_withheld_until_validation_final_ready(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    def fake_provider_turn(**_kwargs):
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="prove a target identity",
            max_iterations=1,
        )
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "DONE withheld because validation/workflow is not final-ready" in output
    report = repo / "fermilink-drvloop" / "validation_report.json"
    assert '"final_ready": false' in report.read_text(encoding="utf-8")


def test_drvloop_publication_depth_rejects_shallow_one_turn_done(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    def fake_provider_turn(**_kwargs):
        _write_passing_target_obligation(repo)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive a publication-quality polariton transport law",
            max_iterations=1,
        )
    )

    assert code == 1
    report = json.loads(
        (repo / "fermilink-drvloop" / "validation_report.json").read_text(
            encoding="utf-8"
        )
    )
    workflow = json.loads(
        (repo / "fermilink-drvloop" / "workflow_state.json").read_text(encoding="utf-8")
    )
    assert report["validation_ready"] is True
    assert report["final_ready"] is False
    assert workflow["summary"]["workflow_ready"] is False
    assert "route_population" in workflow["summary"]["open_stages"]
    assert "turn_budget" in workflow["summary"]["open_stages"]


def test_drvloop_done_is_withheld_when_locked_spec_changes(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    def fake_provider_turn(**_kwargs):
        spec_path = sorted((repo / "projects").glob("*/derivation_spec.yaml"))[-1]
        spec_path.write_text(
            spec_path.read_text(encoding="utf-8").replace(
                "prove the locked target", "prove a weaker target"
            ),
            encoding="utf-8",
        )
        _write_passing_target_obligation(repo)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="prove the locked target",
            max_iterations=1,
        )
    )

    assert code == 1
    output = capsys.readouterr().out
    assert "DONE withheld because validation/workflow is not final-ready" in output
    report = repo / "fermilink-drvloop" / "validation_report.json"
    assert '"spec_integrity_ok": false' in report.read_text(encoding="utf-8")


def test_drvloop_weak_derived_here_obligation_does_not_cover_target(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(
        repo_dir=repo, user_prompt="prove hard target", prompt_file=None
    )
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="prove hard target",
        prompt_file=None,
    )
    project_dir = repo / context.project_rel
    _write_final_artifacts(project_dir)
    (project_dir / "proof_obligations.yaml").write_text(
        "obligations:\n"
        "  - id: broad-derived\n"
        "    type: citation\n"
        "    claim: The final manuscript derives the target claim.\n"
        "    derived_here: true\n"
        "    covers_target_claims: [target-1]\n"
        "    critical: true\n",
        encoding="utf-8",
    )

    report = run_drvloop_validation(repo_dir=repo, spec_context=context)

    assert report["final_ready"] is False
    assert report["target_claims"][0]["status"] == "open"
    assert any(
        "Weak/self-certified coverers: broad-derived" in item["message"]
        for item in report["review_findings"]
    )


def test_drvloop_validation_passes_sympy_target_obligation(tmp_path: Path) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="prove the binomial identity",
        prompt_file=None,
    )
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="prove the binomial identity",
        prompt_file=None,
    )
    _write_passing_target_obligation(repo)

    report = run_drvloop_validation(repo_dir=repo, spec_context=context)

    assert report["final_ready"] is True
    assert report["summary"]["passed"] >= 3
    assert report["target_claims"][0]["status"] == "covered"


def test_drvloop_validation_requires_final_artifacts(tmp_path: Path) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(
        repo_dir=repo,
        user_prompt="prove the target identity",
        prompt_file=None,
    )
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="prove the target identity",
        prompt_file=None,
    )
    project_dir = repo / context.project_rel
    obligations = project_dir / "proof_obligations.yaml"
    obligations.write_text(
        "obligations:\n"
        "  - id: algebra-target\n"
        "    type: algebra\n"
        "    lhs: x + 1\n"
        "    rhs: x + 1\n"
        "    symbols: [x]\n"
        "    covers_target_claims: [target-1]\n"
        "    critical: true\n",
        encoding="utf-8",
    )

    report = run_drvloop_validation(repo_dir=repo, spec_context=context)

    assert report["final_ready"] is False
    blocker_ids = {
        item["id"]
        for item in report["obligations"]
        if item["status"] in {"open", "unknown"}
    }
    assert "final-manuscript-exists" in blocker_ids
    assert "final-pedagogical-note-exists" in blocker_ids


def test_drvloop_auto_extracts_manuscript_gap_obligations(tmp_path: Path) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(repo_dir=repo, user_prompt="derive", prompt_file=None)
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="derive",
        prompt_file=None,
    )
    project_dir = repo / context.project_rel
    manuscript = project_dir / "route-a.md"
    manuscript.write_text(
        "$$ E = m c^2 $$\n"
        "Therefore the Hamiltonian is conserved.\n"
        "This is a standard identity. citation needed\n",
        encoding="utf-8",
    )

    obligations = collect_obligations(repo)

    auto_ids = {item["id"] for item in obligations if item.get("auto_extracted")}
    assert any(item.startswith("auto-equation-") for item in auto_ids)
    assert any(item.startswith("auto-transition-") for item in auto_ids)
    assert any(item.startswith("auto-citation-") for item in auto_ids)
    assert any(item.startswith("auto-hidden-lemma-") for item in auto_ids)
    assert all(
        not item.get("critical") for item in obligations if item.get("auto_extracted")
    )


def test_drvloop_validation_reports_hidden_lemma_reviewer_finding(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(repo_dir=repo, user_prompt="prove", prompt_file=None)
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="prove",
        prompt_file=None,
    )
    project_dir = repo / context.project_rel
    _write_final_artifacts(project_dir)
    (project_dir / "proof_obligations.yaml").write_text(
        "obligations:\n"
        "  - id: hidden-main\n"
        "    type: manual\n"
        "    claim: This standard lemma proves the hard step.\n"
        "    covers_target_claims: [target-1]\n"
        "    critical: true\n",
        encoding="utf-8",
    )

    report = run_drvloop_validation(repo_dir=repo, spec_context=context)

    assert report["final_ready"] is False
    assert report["summary"]["review_critical"] >= 1
    assert any(
        str(item["id"]).startswith("hidden-lemma-")
        for item in report["review_findings"]
    )


def test_drvloop_domain_validators_cover_commutator_and_hermiticity(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(repo_dir=repo, user_prompt="prove", prompt_file=None)
    context = ensure_derivation_spec(
        repo_dir=repo,
        user_prompt="prove",
        prompt_file=None,
    )
    project_dir = repo / context.project_rel
    _write_final_artifacts(project_dir)
    (project_dir / "proof_obligations.yaml").write_text(
        "obligations:\n"
        "  - id: comm-main\n"
        "    type: commutator\n"
        "    lhs: comm(x,p)\n"
        "    rhs: I*hbar\n"
        "    operators: [x, p]\n"
        "    symbols: [hbar]\n"
        "    relations:\n"
        "      comm(x,p): I*hbar\n"
        "  - id: herm-main\n"
        "    type: hermiticity\n"
        "    matrix:\n"
        "      - [1, 0]\n"
        "      - [0, 2]\n"
        "  - id: target-cover\n"
        "    type: algebra\n"
        "    lhs: x\n"
        "    rhs: x\n"
        "    symbols: [x]\n"
        "    covers_target_claims: [target-1]\n"
        "    critical: true\n",
        encoding="utf-8",
    )

    report = run_drvloop_validation(repo_dir=repo, spec_context=context)

    by_id = {item["id"]: item for item in report["obligations"]}
    assert by_id["comm-main"]["status"] == "passed"
    assert by_id["herm-main"]["status"] == "passed"
    assert report["final_ready"] is True


def test_drvloop_workflow_publication_ready_requires_staged_artifacts(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(repo_dir=repo, user_prompt="prove", prompt_file=None)
    context = ensure_derivation_spec(
        repo_dir=repo, user_prompt="prove", prompt_file=None
    )
    project_dir = repo / context.project_rel
    _write_publication_ready_artifacts(project_dir)

    validation = run_drvloop_validation(repo_dir=repo, spec_context=context)
    workflow = evaluate_drvloop_workflow(
        repo_dir=repo,
        spec_context=context,
        validation_report=validation,
        proof_depth="publication",
        iteration=10,
    )
    gated = apply_workflow_gate_to_validation_report(
        repo_dir=repo,
        validation_report=validation,
        workflow_state=workflow,
    )

    assert workflow["summary"]["workflow_ready"] is True
    assert workflow["summary"]["quality_ready"] is True
    assert "latex_exports" not in workflow["stages"]
    assert gated["final_ready"] is True


def test_drvloop_publication_workflow_uses_observed_rounds_when_resumed(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    memory = ensure_drvloop_memory(repo_dir=repo, user_prompt="prove", prompt_file=None)
    memory.write_text(
        memory.read_text(encoding="utf-8")
        + "\n- Round 14 completed before this resumed invocation.\n",
        encoding="utf-8",
    )
    context = ensure_derivation_spec(
        repo_dir=repo, user_prompt="prove", prompt_file=None
    )
    project_dir = repo / context.project_rel
    _write_publication_ready_artifacts(project_dir)

    validation = run_drvloop_validation(repo_dir=repo, spec_context=context)
    workflow = evaluate_drvloop_workflow(
        repo_dir=repo,
        spec_context=context,
        validation_report=validation,
        proof_depth="publication",
        iteration=1,
    )

    assert workflow["current_iteration"] == 14
    assert workflow["stages"]["turn_budget"]["status"] == "complete"


def test_drvloop_publication_sweep_runs_after_final_ready(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    monkeypatch.setattr(drvloop_main.shutil, "which", lambda name: "/usr/bin/pdflatex")
    calls: list[str] = []

    def fake_provider_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        calls.append(prompt)
        specs = sorted((repo / "projects").glob("*/derivation_spec.yaml"))
        assert specs
        project_dir = specs[-1].parent
        if "final publication sweep" in prompt:
            (project_dir / "final_manuscript_aps.tex").write_text(
                "\\documentclass{article}\\begin{document}APS preprint.\\end{document}\n",
                encoding="utf-8",
            )
            (project_dir / "pedagogical_note_grad.tex").write_text(
                "\\documentclass{article}\\begin{document}Graduate note.\\end{document}\n",
                encoding="utf-8",
            )
            (project_dir / "final_manuscript_aps.pdf").write_bytes(b"%PDF-1.4\n")
            (project_dir / "pedagogical_note_grad.pdf").write_bytes(b"%PDF-1.4\n")
            (project_dir / "20_publication_sweep.md").write_text(
                "publication sweep complete\n", encoding="utf-8"
            )
        else:
            _write_publication_ready_artifacts(project_dir)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="derive a publication-ready result",
            max_iterations=10,
            proof_depth="publication",
        )
    )

    assert code == 0
    output = capsys.readouterr().out
    assert "running final publication export sweep" in output
    assert any("final publication sweep" in prompt for prompt in calls)
    assert (repo / "fermilink-drvloop" / "publication_sweep.json").is_file()
    sweep = json.loads(
        (repo / "fermilink-drvloop" / "publication_sweep.json").read_text(
            encoding="utf-8"
        )
    )
    assert sweep["complete"] is True
    project_dir = sorted((repo / "projects").glob("*/derivation_spec.yaml"))[-1].parent
    assert (project_dir / "final_manuscript_aps.tex").is_file()
    assert (project_dir / "pedagogical_note_grad.tex").is_file()
    assert (project_dir / "final_manuscript_aps.pdf").is_file()
    assert (project_dir / "pedagogical_note_grad.pdf").is_file()


def test_drvloop_tex_exports_are_not_auto_scanned_for_obligations(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    ensure_drvloop_memory(repo_dir=repo, user_prompt="prove", prompt_file=None)
    context = ensure_derivation_spec(
        repo_dir=repo, user_prompt="prove", prompt_file=None
    )
    project_dir = repo / context.project_rel
    (project_dir / "final_manuscript_aps.tex").write_text(
        "\\documentclass{article}\n"
        "\\PassOptionsToPackage{unicode}{hyperref}\n"
        "\\begin{document}\n"
        "\\[ x = x \\]\n"
        "Thus the result follows.\n"
        "\\end{document}\n",
        encoding="utf-8",
    )

    obligations = collect_obligations(repo)

    assert not [
        item
        for item in obligations
        if item.get("auto_extracted")
        and item.get("source_file", "").endswith("final_manuscript_aps.tex")
    ]


def test_drvloop_sketch_records_route_level_population(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()

    def fake_provider_turn(**_kwargs):
        specs = sorted((repo / "projects").glob("*/derivation_spec.yaml"))
        project_dir = specs[-1].parent
        _write_final_artifacts(project_dir)
        (project_dir / "proof_obligations.yaml").write_text(
            "obligations:\n"
            "  - id: route-a-target\n"
            "    route_id: route-a\n"
            "    type: algebra\n"
            "    lhs: x + 1\n"
            "    rhs: x + 1\n"
            "    symbols: [x]\n"
            "    covers_target_claims: [target-1]\n"
            "    critical: true\n",
            encoding="utf-8",
        )
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = run_drvloop(
        DrvloopConfig(
            repo_dir=repo,
            user_prompt="prove by route A",
            max_iterations=1,
            proof_depth="quick",
        )
    )

    assert code == 0
    sketches = (repo / "fermilink-drvloop" / "sketches.jsonl").read_text(
        encoding="utf-8"
    )
    assert '"route_id": "route-a"' in sketches
    assert '"validator_pass_count": 1' in sketches


def test_debug_parser_does_not_support_skill_folder() -> None:
    parser = drvloop_main._build_arg_parser()
    args = parser.parse_args(["goal.md", "--max-iterations", "1"])
    assert args.prompt == ["goal.md"]
    option_strings = {
        option
        for action in parser._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings
    assert "--wait-seconds" not in option_strings
    assert "--max-wait-seconds" not in option_strings


def test_cli_drvloop_wires_to_runner_without_git_or_skill_folder(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    goal = repo / "goal.md"
    goal.write_text("derive a compact response equation", encoding="utf-8")
    monkeypatch.chdir(repo)

    captured: dict[str, object] = {}

    def fake_provider_turn(**kwargs):
        captured.update(kwargs)
        _write_passing_target_obligation(repo)
        return {
            "assistant_text": f"done\n{DRVLOOP_DONE_TOKEN}\n",
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(drvloop_main, "_run_provider_turn", fake_provider_turn)

    code = cli.main(
        ["drvloop", "--max-iterations", "1", "--proof-depth", "quick", "goal.md"]
    )

    assert code == 0
    assert not (repo / ".git").exists()
    assert (repo / "fermilink-drvloop" / "state.json").is_file()
    assert not (repo / ".fermilink-drvloop" / "state.json").exists()
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == load_drvloop_guide()
    for alias_name in ("CLAUDE.md", "GEMINI.md"):
        alias_path = repo / alias_name
        assert alias_path.exists() or alias_path.is_symlink()
    assert (repo / "projects" / "memory.md").is_file()
    assert "derive a compact response equation" in str(captured.get("prompt") or "")

    parser = cli._build_parser()
    args = parser.parse_args(["drvloop", "goal.md"])
    assert args.func is cli._cmd_drvloop
    option_strings = {
        option
        for action in parser._subparsers._group_actions[0].choices["drvloop"]._actions
        for option in getattr(action, "option_strings", [])
    }
    assert "--skill-folder" not in option_strings
    assert "--init-git" not in option_strings
    assert "--package" not in option_strings
    assert "--wait-seconds" not in option_strings
    assert "--max-wait-seconds" not in option_strings


def test_cli_drvloop_validation_error_uses_standard_cli_exit(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    repo = tmp_path / "workspace"
    repo.mkdir()
    monkeypatch.chdir(repo)

    code = cli.main(["drvloop", "--max-iterations", "0", "derive"])

    assert code == 2
    assert "max_iterations must be >= 1." in capsys.readouterr().err
