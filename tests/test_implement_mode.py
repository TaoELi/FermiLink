from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.agent_runtime import AgentRuntimePolicy
from fermilink.implement import contract as implement_contract
from fermilink.implement import goal as implement_goal
from fermilink.implement import prompts as implement_prompts
from fermilink.implement import state as implement_state
from fermilink.implement import validation as implement_validation
from fermilink.implement.campaign import run_goal_campaign


SAMPLE_GOAL = """\
# Implementation Goal

## Package
mockpkg

## Target
Add a new alternative SCF routine.

## Editable Scope
- feature.py

## Input API
Expose `run_new_scf(mol, *, max_cycle=50)`.

## Desired Outputs
- Total energy
- Convergence flag

## Representative Workloads
- water-sto3g

## Validation
```
python validate_impl.py
```

## Done Criteria
- API is importable
- Validation score reaches complete=true
"""


def _git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo_dir),
        text=True,
        capture_output=True,
        check=True,
    )
    return (completed.stdout or "").strip()


def _init_git_repo(repo_dir: Path) -> None:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "add", ".")
    _git(
        repo_dir,
        "-c",
        "user.name=Tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "-m",
        "initial",
    )


def test_parse_implement_goal_with_optional_baseline() -> None:
    spec = implement_goal.parse_goal(SAMPLE_GOAL)

    assert implement_goal.is_goal_markdown(SAMPLE_GOAL)
    assert spec["package"] == "mockpkg"
    assert "alternative SCF" in spec["target"]
    assert spec["editable_scope"] == ["feature.py"]
    assert spec["baseline_reference"] == ""
    assert spec["baseline_optional"] is True
    assert len(spec["validation_commands"]) == 1
    assert "validate_impl.py" in spec["validation_commands"][0]
    assert len(spec["done_criteria"]) == 2


def test_implement_goal_prefers_build_section_for_setup_commands(
    tmp_path: Path,
) -> None:
    goal_text = (
        SAMPLE_GOAL
        + "\n## Build\n"
        + "```\npython -m pip install -e .\n```\n"
        + "\n## Pre Commands\n"
        + "```\npython legacy_setup.py\n```\n"
    )
    spec = implement_goal.parse_goal(goal_text)

    assert spec["build_commands"] == ["python -m pip install -e ."]
    assert spec["pre_commands"] == ["python legacy_setup.py"]

    payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=spec,
    )

    assert payload["pre_commands"]["worker"] == [
        ["python", "-m", "pip", "install", "-e", "."]
    ]
    assert payload["pre_commands"]["controller"] == [
        ["python", "-m", "pip", "install", "-e", "."]
    ]


def test_implement_goal_keeps_pre_commands_as_legacy_alias(
    tmp_path: Path,
) -> None:
    goal_text = (
        SAMPLE_GOAL + "\n## Pre Commands\n" + "```\npython legacy_setup.py\n```\n"
    )
    spec = implement_goal.parse_goal(goal_text)

    assert spec["build_commands"] == []
    assert spec["pre_commands"] == ["python legacy_setup.py"]

    payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=spec,
    )

    assert payload["pre_commands"]["worker"] == [["python", "legacy_setup.py"]]
    assert payload["pre_commands"]["controller"] == [["python", "legacy_setup.py"]]


def test_default_contract_keeps_baseline_optional_and_progressive(
    tmp_path: Path,
) -> None:
    (tmp_path / "feature.py").write_text("VALUE = 0\n", encoding="utf-8")
    spec = implement_goal.parse_goal(SAMPLE_GOAL)

    payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=spec,
        analysis={"proposed_api": "run_new_scf(mol)"},
    )

    assert payload["baseline"]["mode"] == "exploratory"
    assert payload["baseline"]["optional"] is True
    assert payload["api"]["input"] == "Expose `run_new_scf(mol, *, max_cycle=50)`."
    assert payload["repo"]["editable_paths"] == ["feature.py"]
    assert payload["validation"]["allow_partial_improvements"] is True
    assert payload["validation"]["commands"] == [["python", "validate_impl.py"]]


def test_goal_validation_prose_does_not_become_shell_commands(tmp_path: Path) -> None:
    goal_text = SAMPLE_GOAL.replace(
        "## Validation\n```\npython validate_impl.py\n```\n",
        "## Validation\nUse scientific checks for the requested API and outputs.\n",
    )
    spec = implement_goal.parse_goal(goal_text)

    assert spec["validation_commands"] == []

    payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=spec,
    )

    assert payload["validation"]["commands"] == []


def test_worker_visible_contract_redacts_controller_holdouts(tmp_path: Path) -> None:
    goal_text = SAMPLE_GOAL.replace(
        "- water-sto3g",
        "- train-water-sto3g: public worker case\n"
        "- test-secret-ammonia: controller-only holdout",
    )
    spec = implement_goal.parse_goal(goal_text)
    payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=spec,
    )

    split = implement_contract.workload_split(payload)
    assert split["enabled"] is True
    assert split["source"] == "prefix"
    assert split["worker_workload_ids"] == ["train-water-sto3g"]
    assert split["controller_workload_ids"] == ["test-secret-ammonia"]

    worker_payload = implement_contract.worker_visible_contract(payload)
    rendered = json.dumps(worker_payload, sort_keys=True)
    assert "train-water-sto3g" in rendered
    assert "test-secret-ammonia" not in rendered
    assert worker_payload["validation"]["commands_hidden_from_worker"] is True
    assert "commands" not in worker_payload["validation"]


def test_worker_visible_goal_redacts_controller_holdouts() -> None:
    goal_text = SAMPLE_GOAL.replace(
        "- water-sto3g",
        "- train-water-sto3g: public worker case\n"
        "- test-secret-ammonia: controller-only holdout",
    )

    rendered = implement_goal.render_worker_visible_goal(
        goal_text,
        worker_workloads=["train-water-sto3g: public worker case"],
        split_enabled=True,
    )

    assert "train-water-sto3g" in rendered
    assert "test-secret-ammonia" not in rendered
    assert "python validate_impl.py" not in rendered
    assert "validation is generated in the YAML contract" in rendered


def test_validation_accepts_structured_partial_score(tmp_path: Path) -> None:
    contract_payload = implement_contract.build_default_contract(
        tmp_path,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )
    contract_path = (
        tmp_path / ".fermilink-implement" / "autogen" / "implementation_contract.yaml"
    )
    implement_contract.write_contract(contract_path, contract_payload)
    (tmp_path / "validate_impl.py").write_text(
        (
            "import json\n"
            "print(json.dumps({\n"
            "  'ok': False,\n"
            "  'score': 45.0,\n"
            "  'complete': False,\n"
            "  'build_ok': True,\n"
            "  'api_ok': True,\n"
            "  'milestones': [{'id': 'api', 'status': 'pass', 'score': 45.0}],\n"
            "}))\n"
        ),
        encoding="utf-8",
    )

    result = implement_validation.run_validation_suite(
        tmp_path,
        contract_payload=contract_payload,
        contract_path=contract_path,
        run_dir=tmp_path / ".fermilink-implement" / "runs" / "validation",
        timeout_seconds=30,
    )

    assert result["score"] == 45.0
    assert result["complete"] is False
    assert result["api_ok"] is True
    assert result["hard_reject"] is False


def test_acceptance_allows_controller_approved_partial_improvement() -> None:
    contract_payload = implement_contract.build_default_contract(
        Path("."),
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )

    decision = implement_validation.acceptance_decision(
        contract_payload=contract_payload,
        incumbent_validation={"score": 10.0, "complete": False},
        candidate_validation={"score": 25.0, "complete": False},
        controller_decision="ACCEPTED",
        hard_reject=False,
        hard_reason="",
    )

    assert decision["accepted"] is True
    assert decision["final_complete"] is False
    assert decision["status"] == "accepted_partial"


def test_acceptance_rejects_incomplete_final_integrity() -> None:
    contract_payload = implement_contract.build_default_contract(
        Path("."),
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )

    decision = implement_validation.acceptance_decision(
        contract_payload=contract_payload,
        incumbent_validation={"score": 95.0, "complete": False},
        candidate_validation={
            "score": 100.0,
            "complete": True,
            "ok": True,
            "build_ok": True,
            "api_ok": True,
            "scientific_checks_ok": False,
        },
        controller_decision="ACCEPTED",
        hard_reject=False,
        hard_reason="",
    )

    assert decision["accepted"] is False
    assert decision["final_complete"] is False
    assert "complete=true" in decision["reason"]


def test_acceptance_requires_structured_controller_review_for_final_completion() -> (
    None
):
    contract_payload = implement_contract.build_default_contract(
        Path("."),
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )
    candidate_validation = {
        "score": 100.0,
        "complete": True,
        "ok": True,
        "build_ok": True,
        "api_ok": True,
        "scientific_checks_ok": True,
        "commands_ok": True,
    }

    decision = implement_validation.acceptance_decision(
        contract_payload=contract_payload,
        incumbent_validation={"score": 10.0, "complete": False},
        candidate_validation=candidate_validation,
        controller_decision="ACCEPTED",
        hard_reject=False,
        hard_reason="",
    )

    assert decision["accepted"] is True
    assert decision["final_complete"] is False
    assert decision["status"] == "accepted_partial"
    assert "structured final target-satisfaction evidence" in decision["reason"]


def test_acceptance_allows_final_completion_with_controller_evidence() -> None:
    contract_payload = implement_contract.build_default_contract(
        Path("."),
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )

    decision = implement_validation.acceptance_decision(
        contract_payload=contract_payload,
        incumbent_validation={"score": 50.0, "complete": False},
        candidate_validation={
            "score": 100.0,
            "complete": True,
            "ok": True,
            "build_ok": True,
            "api_ok": True,
            "scientific_checks_ok": True,
            "commands_ok": True,
        },
        controller_decision=None,
        controller_review={
            "decision": "ACCEPTED",
            "final_complete": True,
            "requirements": [
                {
                    "id": "api",
                    "verdict": "pass",
                    "required": True,
                    "evidence": ["feature.py exposes run_new_scf"],
                },
                {
                    "id": "algorithm",
                    "verdict": "pass",
                    "required": True,
                    "evidence": ["diff uses iterative solver path"],
                },
            ],
        },
        hard_reject=False,
        hard_reason="",
    )

    assert decision["accepted"] is True
    assert decision["final_complete"] is True
    assert decision["status"] == "complete"


def test_controller_review_parser_normalizes_structured_review() -> None:
    review = implement_prompts.extract_controller_review(
        """
<controller_review>{
  "decision": "accepted",
  "final_complete": true,
  "summary": "target satisfied",
  "requirements": [
    {"id": "api", "verdict": "PASS", "evidence": "feature.py"}
  ]
}</controller_review>
"""
    )

    assert review is not None
    assert review["decision"] == "ACCEPTED"
    assert review["summary"] == "target satisfied"
    assert review["requirements"][0]["verdict"] == "pass"
    assert review["requirements"][0]["evidence"] == ["feature.py"]


def test_implement_plan_only_initializes_standalone_workspace(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text("print('ok')\n", encoding="utf-8")
    _init_git_repo(repo)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=True,
            resume=False,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=None,
            stop_on_consecutive_rejections=None,
            worker_max_iterations=None,
            worker_wait_seconds=None,
            worker_max_wait_seconds=None,
            worker_pid_stall_seconds=None,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=None,
            forever=False,
        )
    )

    assert result["status"] == "planned"
    assert (repo / ".fermilink-implement" / "autogen" / "goal.md").is_file()
    assert (
        repo / ".fermilink-implement" / "autogen" / "implementation_contract.yaml"
    ).is_file()
    assert (
        repo / ".fermilink-implement" / "autogen" / "implementation_plan.md"
    ).is_file()
    state_payload = json.loads(
        (repo / ".fermilink-implement" / "state.json").read_text(encoding="utf-8")
    )
    assert state_payload["package_id"] == "mockpkg"


def test_implement_campaign_accepts_partial_progress_from_worker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text(
        (
            "import json\n"
            "from pathlib import Path\n"
            "text = Path('feature.py').read_text(encoding='utf-8')\n"
            "score = 0.0\n"
            "complete = False\n"
            "if 'PARTIAL' in text:\n"
            "    score = 50.0\n"
            "elif 'COMPLETE' in text:\n"
            "    score = 100.0\n"
            "    complete = True\n"
            "print(json.dumps({\n"
            "    'ok': score > 0,\n"
            "    'score': score,\n"
            "    'complete': complete,\n"
            "    'build_ok': True,\n"
            "    'api_ok': score > 0,\n"
            "    'milestones': [{'id': 'implementation', 'status': 'pass' if score else 'fail', 'score': score}],\n"
            "}))\n"
        ),
        encoding="utf-8",
    )
    _init_git_repo(repo)

    implement_state.ensure_autogen_root(repo)
    contract_payload = implement_contract.build_default_contract(
        repo,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )
    contract_payload["validation"]["commands"] = [[sys.executable, "validate_impl.py"]]
    contract_payload["campaign"]["max_iterations"] = 1
    implement_contract.write_contract(
        implement_state.contract_path(repo), contract_payload
    )

    calls: list[str] = []
    worker_git_was_visible = False

    def fake_run_exec_chat_turn(**kwargs):
        nonlocal worker_git_was_visible
        prompt = str(kwargs.get("prompt") or "")
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        if "Validation context:" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>honest partial implementation</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        calls.append("worker")
        worker_git_was_visible = (repo_dir / ".git").exists()
        _git(repo_dir, "status", "--porcelain")
        (repo_dir / "feature.py").write_text("VALUE = 'PARTIAL'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<implementation_description>partial api implementation</implementation_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)
    tagged_messages: list[tuple[str, str]] = []
    original_print_tagged = cli._print_tagged

    def fake_print_tagged(tag, message, *args, **kwargs):
        tagged_messages.append((str(tag), str(message)))
        return original_print_tagged(tag, message, *args, **kwargs)

    monkeypatch.setattr(cli, "_print_tagged", fake_print_tagged)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=False,
            resume=True,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=1,
            stop_on_consecutive_rejections=1,
            worker_max_iterations=1,
            worker_wait_seconds=0,
            worker_max_wait_seconds=1,
            worker_pid_stall_seconds=1,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=30,
            forever=False,
        )
    )

    assert calls == ["worker", "controller"]
    assert worker_git_was_visible is True
    assert ("implement", "iteration 1/1") in tagged_messages
    assert not any(
        tag == "optimize" and message == "iteration 1/1"
        for tag, message in tagged_messages
    )
    assert result["accepted_count"] == 1
    assert result["complete"] is False
    assert "PARTIAL" in (repo / "feature.py").read_text(encoding="utf-8")
    state_payload = json.loads(
        (repo / ".fermilink-implement" / "state.json").read_text(encoding="utf-8")
    )
    assert state_payload["api_locked"] is True
    assert state_payload["api_locked_at_iteration"] == 1
    results_text = (repo / ".fermilink-implement" / "results.tsv").read_text(
        encoding="utf-8"
    )
    assert "\taccepted_partial\t50\tfalse\t" in results_text


def test_implement_campaign_completes_with_structured_controller_review(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text(
        (
            "import json\n"
            "from pathlib import Path\n"
            "text = Path('feature.py').read_text(encoding='utf-8')\n"
            "score = 100.0 if 'COMPLETE' in text else 0.0\n"
            "print(json.dumps({\n"
            "    'ok': score == 100.0,\n"
            "    'score': score,\n"
            "    'complete': score == 100.0,\n"
            "    'build_ok': True,\n"
            "    'api_ok': score == 100.0,\n"
            "    'scientific_checks_ok': score == 100.0,\n"
            "}))\n"
        ),
        encoding="utf-8",
    )
    _init_git_repo(repo)

    implement_state.ensure_autogen_root(repo)
    contract_payload = implement_contract.build_default_contract(
        repo,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )
    contract_payload["validation"]["commands"] = [[sys.executable, "validate_impl.py"]]
    contract_payload["campaign"]["max_iterations"] = 1
    implement_contract.write_contract(
        implement_state.contract_path(repo), contract_payload
    )

    calls: list[str] = []

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        if "Validation context:" in prompt:
            calls.append("controller")
            return {
                "assistant_text": (
                    "<controller_review>{"
                    '"decision": "ACCEPTED", '
                    '"final_complete": true, '
                    '"summary": "target satisfied by code review", '
                    '"requirements": ['
                    '{"id": "api", "verdict": "pass", "required": true, '
                    '"evidence": ["feature.py implements requested API"]}, '
                    '{"id": "honest_implementation", "verdict": "pass", "required": true, '
                    '"evidence": ["candidate.diff changes implementation code"]}'
                    "]}</controller_review>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        calls.append("worker")
        (repo_dir / "feature.py").write_text("VALUE = 'COMPLETE'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<implementation_description>complete implementation</implementation_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=False,
            resume=True,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=1,
            stop_on_consecutive_rejections=1,
            worker_max_iterations=1,
            worker_wait_seconds=0,
            worker_max_wait_seconds=1,
            worker_pid_stall_seconds=1,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=30,
            forever=False,
        )
    )

    assert calls == ["worker", "controller"]
    assert result["accepted_count"] == 1
    assert result["complete"] is True
    controller_result = json.loads(
        (
            repo
            / ".fermilink-implement"
            / "runs"
            / "iter_0001"
            / "controller_result.json"
        ).read_text(encoding="utf-8")
    )
    assert controller_result["decision"] == "ACCEPTED"
    assert controller_result["controller_review"]["final_complete"] is True


def test_implement_worker_receives_redacted_holdout_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    split_goal = SAMPLE_GOAL.replace(
        "- water-sto3g",
        "- train-water-sto3g: visible worker case\n"
        "- test-secret-ammonia: hidden controller holdout",
    )
    (repo / "goal.md").write_text(split_goal, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text(
        (
            "import json\n"
            "from pathlib import Path\n"
            "score = 50.0 if 'PARTIAL' in Path('feature.py').read_text(encoding='utf-8') else 0.0\n"
            "print(json.dumps({\n"
            "    'ok': score > 0,\n"
            "    'score': score,\n"
            "    'complete': False,\n"
            "    'build_ok': True,\n"
            "    'api_ok': score > 0,\n"
            "    'scientific_checks_ok': score > 0,\n"
            "}))\n"
        ),
        encoding="utf-8",
    )
    _init_git_repo(repo)

    implement_state.ensure_autogen_root(repo)
    contract_payload = implement_contract.build_default_contract(
        repo,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(split_goal),
    )
    contract_payload["validation"]["commands"] = [[sys.executable, "validate_impl.py"]]
    contract_payload["campaign"]["max_iterations"] = 1
    implement_contract.write_contract(
        implement_state.contract_path(repo), contract_payload
    )

    worker_saw_redacted_artifacts = False

    def fake_run_exec_chat_turn(**kwargs):
        nonlocal worker_saw_redacted_artifacts
        prompt = str(kwargs.get("prompt") or "")
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        if "Validation context:" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>honest partial implementation</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        worker_goal = (repo_dir / "goal.md").read_text(encoding="utf-8")
        worker_contract = (
            repo_dir
            / ".fermilink-implement"
            / "autogen"
            / "implementation_contract.yaml"
        ).read_text(encoding="utf-8")
        worker_saw_redacted_artifacts = (
            "train-water-sto3g" in worker_goal
            and "test-secret-ammonia" not in worker_goal
            and "test-secret-ammonia" not in worker_contract
            and "validate_impl.py" not in worker_contract
            and "commands_hidden_from_worker" in worker_contract
        )
        (repo_dir / "feature.py").write_text("VALUE = 'PARTIAL'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<implementation_description>partial implementation</implementation_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=False,
            resume=True,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=1,
            stop_on_consecutive_rejections=1,
            worker_max_iterations=1,
            worker_wait_seconds=0,
            worker_max_wait_seconds=1,
            worker_pid_stall_seconds=1,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=30,
            forever=False,
        )
    )

    assert worker_saw_redacted_artifacts is True
    assert result["accepted_count"] == 1


def test_source_analysis_rejects_tracked_source_side_effects(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text("print('ok')\n", encoding="utf-8")
    _init_git_repo(repo)

    monkeypatch.setattr(
        cli,
        "resolve_agent_runtime_policy",
        lambda: AgentRuntimePolicy(provider="codex", sandbox_policy="bypass"),
    )

    def fake_run_exec_chat_turn(**kwargs):
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        (repo_dir / "feature.py").write_text(
            "VALUE = 'agent side effect'\n", encoding="utf-8"
        )
        return {
            "assistant_text": (
                '<source_analysis>{"proposed_api": "run_new_scf(mol)"}</source_analysis>'
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    with pytest.raises(cli.PackageError, match="source analysis left tracked changes"):
        run_goal_campaign(
            argparse.Namespace(
                goal="goal.md",
                project_root=str(repo),
                plan_only=False,
                resume=False,
                baseline_only=False,
                allow_dirty=False,
                branch=None,
                sandbox=None,
                max_iterations=1,
                stop_on_consecutive_rejections=1,
                worker_max_iterations=1,
                worker_wait_seconds=0,
                worker_max_wait_seconds=1,
                worker_pid_stall_seconds=1,
                worker_provider=None,
                worker_model=None,
                timeout_seconds=30,
                forever=False,
            )
        )


def test_worker_cannot_sync_immutable_artifacts_even_with_broad_scope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    broad_goal = SAMPLE_GOAL.replace("- feature.py", "- **/*")
    (repo / "goal.md").write_text(broad_goal, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text(
        (
            "import json\n"
            "from pathlib import Path\n"
            "text = Path('feature.py').read_text(encoding='utf-8')\n"
            "score = 50.0 if 'PARTIAL' in text else 0.0\n"
            "print(json.dumps({\n"
            "    'ok': score > 0,\n"
            "    'score': score,\n"
            "    'complete': False,\n"
            "    'build_ok': True,\n"
            "    'api_ok': score > 0,\n"
            "    'scientific_checks_ok': score > 0,\n"
            "}))\n"
        ),
        encoding="utf-8",
    )
    _init_git_repo(repo)

    implement_state.ensure_autogen_root(repo)
    contract_payload = implement_contract.build_default_contract(
        repo,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(broad_goal),
    )
    contract_payload["validation"]["commands"] = [[sys.executable, "validate_impl.py"]]
    contract_payload["campaign"]["max_iterations"] = 1
    implement_contract.write_contract(
        implement_state.contract_path(repo), contract_payload
    )
    original_contract_text = implement_state.contract_path(repo).read_text(
        encoding="utf-8"
    )

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        if "Validation context:" in prompt:
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>honest partial implementation</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "feature.py").write_text("VALUE = 'PARTIAL'\n", encoding="utf-8")
        (
            repo_dir
            / ".fermilink-implement"
            / "autogen"
            / "implementation_contract.yaml"
        ).write_text(
            "schema_version: 999\n",
            encoding="utf-8",
        )
        return {
            "assistant_text": (
                "<implementation_description>partial api implementation</implementation_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=False,
            resume=True,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=1,
            stop_on_consecutive_rejections=1,
            worker_max_iterations=1,
            worker_wait_seconds=0,
            worker_max_wait_seconds=1,
            worker_pid_stall_seconds=1,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=30,
            forever=False,
        )
    )

    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 1
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 'base'\n"
    assert (
        implement_state.contract_path(repo).read_text(encoding="utf-8")
        == original_contract_text
    )


def test_controller_tracked_side_effect_rejects_candidate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "goal.md").write_text(SAMPLE_GOAL, encoding="utf-8")
    (repo / "feature.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    (repo / "other.py").write_text("UNCHANGED = True\n", encoding="utf-8")
    (repo / "validate_impl.py").write_text(
        (
            "import json\n"
            "from pathlib import Path\n"
            "text = Path('feature.py').read_text(encoding='utf-8')\n"
            "score = 50.0 if 'PARTIAL' in text else 0.0\n"
            "print(json.dumps({\n"
            "    'ok': score > 0,\n"
            "    'score': score,\n"
            "    'complete': False,\n"
            "    'build_ok': True,\n"
            "    'api_ok': score > 0,\n"
            "    'scientific_checks_ok': score > 0,\n"
            "}))\n"
        ),
        encoding="utf-8",
    )
    _init_git_repo(repo)

    implement_state.ensure_autogen_root(repo)
    contract_payload = implement_contract.build_default_contract(
        repo,
        package_id="mockpkg",
        goal_spec=implement_goal.parse_goal(SAMPLE_GOAL),
    )
    contract_payload["validation"]["commands"] = [[sys.executable, "validate_impl.py"]]
    contract_payload["campaign"]["max_iterations"] = 1
    implement_contract.write_contract(
        implement_state.contract_path(repo), contract_payload
    )

    def fake_run_exec_chat_turn(**kwargs):
        prompt = str(kwargs.get("prompt") or "")
        repo_dir = Path(str(kwargs.get("repo_dir") or ""))
        if "Validation context:" in prompt:
            (repo_dir / "other.py").write_text("UNCHANGED = False\n", encoding="utf-8")
            return {
                "assistant_text": (
                    "<decision>ACCEPTED</decision>\n"
                    "<controller_summary>side effect should reject</controller_summary>"
                ),
                "return_code": 0,
                "stderr": "",
            }
        (repo_dir / "feature.py").write_text("VALUE = 'PARTIAL'\n", encoding="utf-8")
        return {
            "assistant_text": (
                "<implementation_description>partial api implementation</implementation_description>\n"
                f"{cli.LOOP_DONE_TOKEN}\n"
            ),
            "return_code": 0,
            "stderr": "",
        }

    monkeypatch.setattr(cli, "_run_exec_chat_turn", fake_run_exec_chat_turn)

    result = run_goal_campaign(
        argparse.Namespace(
            goal="goal.md",
            project_root=str(repo),
            plan_only=False,
            resume=True,
            baseline_only=False,
            allow_dirty=False,
            branch=None,
            sandbox=None,
            max_iterations=1,
            stop_on_consecutive_rejections=1,
            worker_max_iterations=1,
            worker_wait_seconds=0,
            worker_max_wait_seconds=1,
            worker_pid_stall_seconds=1,
            worker_provider=None,
            worker_model=None,
            timeout_seconds=30,
            forever=False,
        )
    )

    assert result["accepted_count"] == 0
    assert result["rejected_count"] == 1
    assert (repo / "feature.py").read_text(encoding="utf-8") == "VALUE = 'base'\n"
    assert (repo / "other.py").read_text(encoding="utf-8") == "UNCHANGED = True\n"
