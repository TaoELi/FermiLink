from __future__ import annotations

import json
from pathlib import Path

import pytest

from fermilink import cli
from fermilink.cli.commands import workflows as workflow_commands


def _scan_limits() -> dict[str, int]:
    return {
        "max_files": 4000,
        "max_total_bytes": 1_073_741_824,
        "max_file_bytes": 67_108_864,
        "hash_max_bytes": 1_048_576,
    }


def _data_context(
    *,
    source_data_dir: Path,
    run_dir: Path,
    mapping_thresholds: dict[str, int] | None = None,
) -> dict[str, object]:
    data_root_rel = run_dir.relative_to(run_dir.parents[2]).as_posix()
    thresholds = (
        dict(mapping_thresholds)
        if isinstance(mapping_thresholds, dict)
        else workflow_commands._default_data_mapping_thresholds()
    )
    return {
        "enabled": True,
        "workflow": "reproduce",
        "source_path": str(source_data_dir),
        "source_path_input": "input_data",
        "read_only": True,
        "limits": _scan_limits(),
        "filter_settings": workflow_commands._default_data_filter_settings(),
        "mapping_thresholds": thresholds,
        "artifacts": {
            "root": f"{data_root_rel}/data",
            "manifest": f"{data_root_rel}/data/data_manifest.json",
            "manifest_compact": f"{data_root_rel}/data/data_manifest.json",
            "manifest_full": f"{data_root_rel}/data/data_manifest_full.json",
            "summary": f"{data_root_rel}/data/data_summary.md",
            "task_map": f"{data_root_rel}/data/task_data_map.json",
        },
    }


def _compact_manifest_payload(source_data_dir: Path) -> dict[str, object]:
    files = [
        {
            "path": "inputs/base.json",
            "size": 16,
            "mtime_ns": 1,
            "type": "structured_text",
            "family_count": 1,
            "family_examples": [],
        },
        {
            "path": "tables/results.csv",
            "size": 24,
            "mtime_ns": 2,
            "type": "tabular_or_text",
            "family_count": 1,
            "family_examples": [],
        },
        {
            "path": "scripts/plot.py",
            "size": 128,
            "mtime_ns": 3,
            "type": "code",
            "family_count": 1,
            "family_examples": [],
        },
    ]
    return {
        "manifest_kind": "compact",
        "schema_version": 2,
        "filter_rules_version": 1,
        "data_dir": str(source_data_dir),
        "scan_limits": _scan_limits(),
        "filter_settings": workflow_commands._default_data_filter_settings(),
        "full_manifest_fingerprint": "full-fp",
        "fingerprint": "compact-fp",
        "files": files,
        "skipped": [],
        "stats": {
            "indexed_files": len(files),
            "indexed_bytes": 168,
            "skipped_files": 0,
            "truncated": False,
            "truncated_reason": "",
            "full_indexed_files": len(files),
            "full_indexed_bytes": 168,
            "excluded_files": 0,
            "excluded_count_by_rule": {},
            "family_collapsed_files": 0,
            "family_group_count": len(files),
            "prompt_char_estimate": 600,
        },
    }


def _full_manifest_payload(source_data_dir: Path) -> dict[str, object]:
    compact = _compact_manifest_payload(source_data_dir)
    return {
        "manifest_kind": "full",
        "schema_version": 2,
        "filter_rules_version": 1,
        "data_dir": str(source_data_dir),
        "scan_limits": _scan_limits(),
        "filter_settings": workflow_commands._default_data_filter_settings(),
        "fingerprint": "full-fp",
        "files": [
            {
                "path": item["path"],
                "size": item["size"],
                "mtime_ns": item["mtime_ns"],
                "type": item["type"],
            }
            for item in compact["files"]
        ],
        "skipped": [],
        "stats": {
            "indexed_files": 3,
            "indexed_bytes": 168,
            "skipped_files": 0,
            "truncated": False,
            "truncated_reason": "",
        },
    }


def _planner_plan_payload() -> dict[str, object]:
    return {
        "version": 1,
        "paper_source": "paper.md",
        "assumptions": [],
        "tasks": [
            {
                "id": "task_001",
                "title": "task one",
                "objective": "use base input",
                "prompt_markdown": "run task one",
            },
            {
                "id": "task_002",
                "title": "task two",
                "objective": "build final table",
                "prompt_markdown": "run task two",
            },
        ],
    }


def _task_map_for_task(task_id: str, path: str) -> dict[str, object]:
    return {
        "version": 1,
        "tasks": [
            {
                "id": task_id,
                "files": [
                    {
                        "path": path,
                        "rationale": f"mapped for {task_id}",
                        "confidence": 0.8,
                    }
                ],
                "unknowns": [],
                "notes": [],
            }
        ],
        "global_unknowns": [],
    }


def test_prepare_data_artifacts_generates_full_and_compact_with_filtering_and_family_collapse(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    source_data_dir = repo_dir / "input_data"
    (source_data_dir / "results").mkdir(parents=True, exist_ok=True)
    (source_data_dir / "__pycache__").mkdir(parents=True, exist_ok=True)

    (source_data_dir / "results" / "run_001.csv").write_text("x,y\n1,2\n", encoding="utf-8")
    (source_data_dir / "results" / "run_002.csv").write_text("x,y\n2,3\n", encoding="utf-8")
    (source_data_dir / "results" / "run_003.csv").write_text("x,y\n3,4\n", encoding="utf-8")
    (source_data_dir / "inputs.json").write_text('{"a": 1}\n', encoding="utf-8")
    (source_data_dir / "slurm-12345.out").write_text("log\n", encoding="utf-8")
    (source_data_dir / "slurm-12345.err").write_text("err\n", encoding="utf-8")
    (source_data_dir / "nohup.out").write_text("tmp\n", encoding="utf-8")
    (source_data_dir / "__pycache__" / "file.pyc").write_bytes(b"\x00\x01")

    data_context = _data_context(source_data_dir=source_data_dir, run_dir=run_dir)
    prepared = workflow_commands._prepare_workflow_data_artifacts(
        repo_dir=repo_dir,
        run_dir=run_dir,
        data_context=data_context,
    )
    artifacts = prepared.get("artifacts")
    assert isinstance(artifacts, dict)
    compact_manifest_path = repo_dir / str(artifacts["manifest_compact"])
    full_manifest_path = repo_dir / str(artifacts["manifest_full"])
    compact_manifest = json.loads(compact_manifest_path.read_text(encoding="utf-8"))
    full_manifest = json.loads(full_manifest_path.read_text(encoding="utf-8"))

    assert compact_manifest["manifest_kind"] == "compact"
    assert full_manifest["manifest_kind"] == "full"

    compact_paths = {str(item.get("path")) for item in compact_manifest.get("files", [])}
    assert "slurm-12345.out" not in compact_paths
    assert "slurm-12345.err" not in compact_paths
    assert "nohup.out" not in compact_paths
    assert "__pycache__/file.pyc" not in compact_paths

    full_paths = {str(item.get("path")) for item in full_manifest.get("files", [])}
    assert "slurm-12345.out" in full_paths
    assert "slurm-12345.err" in full_paths

    run_entries = [
        item
        for item in compact_manifest.get("files", [])
        if str(item.get("path", "")).startswith("results/run_")
    ]
    assert len(run_entries) == 1
    assert int(run_entries[0].get("family_count") or 0) == 3
    assert run_entries[0].get("family_examples")

    excluded_by_rule = compact_manifest["stats"]["excluded_count_by_rule"]
    assert int(excluded_by_rule.get("slurm_output_noise") or 0) == 2


def test_data_auditor_prompt_excludes_source_text_and_small_mode_uses_single_global_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    data_root = run_dir / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    source_data_dir = repo_dir / "input_data"
    source_data_dir.mkdir(parents=True, exist_ok=True)

    compact_manifest = _compact_manifest_payload(source_data_dir)
    full_manifest = _full_manifest_payload(source_data_dir)
    (data_root / "data_manifest.json").write_text(
        json.dumps(compact_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_manifest_full.json").write_text(
        json.dumps(full_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_summary.md").write_text("# Data Summary\n", encoding="utf-8")

    planner_plan = _planner_plan_payload()
    task_map_payload = {
        "version": 1,
        "tasks": [
            {
                "id": "task_001",
                "files": [
                    {
                        "path": "inputs/base.json",
                        "rationale": "task1",
                        "confidence": 0.8,
                    }
                ],
                "unknowns": [],
                "notes": [],
            },
            {
                "id": "task_002",
                "files": [
                    {
                        "path": "tables/results.csv",
                        "rationale": "task2",
                        "confidence": 0.8,
                    }
                ],
                "unknowns": [],
                "notes": [],
            },
        ],
        "global_unknowns": [],
    }
    prompts: list[str] = []

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        prompts.append(prompt)
        if "workflow data auditor mode" in prompt:
            return {
                "return_code": 0,
                "assistant_text": "<task_data_map>"
                + json.dumps(task_map_payload)
                + "</task_data_map>",
                "stderr": "",
            }
        return {
            "return_code": 0,
            "assistant_text": "<reproduce_plan>"
            + json.dumps(planner_plan)
            + "</reproduce_plan>",
            "stderr": "",
        }

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)
    data_context = _data_context(
        source_data_dir=source_data_dir,
        run_dir=run_dir,
        mapping_thresholds={
            "large_threshold_files": 9999,
            "large_threshold_chars": 999999,
            "task_slice_max_files": 120,
            "task_slice_max_chars": 45000,
        },
    )
    source_text = "SUPER_SECRET_SOURCE_TEXT"
    plan = cli._generate_reproduce_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text=source_text,
        source_description="paper.md",
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        data_context=data_context,
    )
    assert plan["version"] == 1
    data_prompts = [p for p in prompts if "workflow data auditor mode" in p]
    assert len(data_prompts) == 1
    assert source_text not in data_prompts[0]
    assert "Mapping mode: global" in data_prompts[0]


def test_large_mode_calls_data_auditor_per_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    data_root = run_dir / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    source_data_dir = repo_dir / "input_data"
    source_data_dir.mkdir(parents=True, exist_ok=True)

    compact_manifest = _compact_manifest_payload(source_data_dir)
    full_manifest = _full_manifest_payload(source_data_dir)
    (data_root / "data_manifest.json").write_text(
        json.dumps(compact_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_manifest_full.json").write_text(
        json.dumps(full_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_summary.md").write_text("# Data Summary\n", encoding="utf-8")

    planner_plan = _planner_plan_payload()
    prompts: list[str] = []

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        prompts.append(prompt)
        if "workflow data auditor mode" in prompt and '"id": "task_001"' in prompt:
            payload = _task_map_for_task("task_001", "inputs/base.json")
            return {
                "return_code": 0,
                "assistant_text": "<task_data_map>"
                + json.dumps(payload)
                + "</task_data_map>",
                "stderr": "",
            }
        if "workflow data auditor mode" in prompt and '"id": "task_002"' in prompt:
            payload = _task_map_for_task("task_002", "tables/results.csv")
            return {
                "return_code": 0,
                "assistant_text": "<task_data_map>"
                + json.dumps(payload)
                + "</task_data_map>",
                "stderr": "",
            }
        return {
            "return_code": 0,
            "assistant_text": "<reproduce_plan>"
            + json.dumps(planner_plan)
            + "</reproduce_plan>",
            "stderr": "",
        }

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)
    data_context = _data_context(
        source_data_dir=source_data_dir,
        run_dir=run_dir,
        mapping_thresholds={
            "large_threshold_files": 1,
            "large_threshold_chars": 1,
            "task_slice_max_files": 120,
            "task_slice_max_chars": 45000,
        },
    )
    plan = cli._generate_reproduce_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text="source",
        source_description="paper.md",
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        data_context=data_context,
    )
    assert plan["version"] == 1
    data_prompts = [p for p in prompts if "workflow data auditor mode" in p]
    assert len(data_prompts) == 2
    assert all("Mapping mode: per_task_large" in prompt for prompt in data_prompts)

    task_map = json.loads((data_root / "task_data_map.json").read_text(encoding="utf-8"))
    assert task_map["mapping_mode"] == "per_task_large"
    tasks = task_map.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) == 2


def test_large_mode_falls_back_per_task_without_failing_whole_mapping(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    run_dir = repo_dir / "projects" / "reproduce" / "run-001"
    data_root = run_dir / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    source_data_dir = repo_dir / "input_data"
    source_data_dir.mkdir(parents=True, exist_ok=True)

    compact_manifest = _compact_manifest_payload(source_data_dir)
    full_manifest = _full_manifest_payload(source_data_dir)
    (data_root / "data_manifest.json").write_text(
        json.dumps(compact_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_manifest_full.json").write_text(
        json.dumps(full_manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    (data_root / "data_summary.md").write_text("# Data Summary\n", encoding="utf-8")

    planner_plan = _planner_plan_payload()
    prompts: list[str] = []

    def fake_exec_turn(**kwargs) -> dict[str, object]:
        prompt = str(kwargs.get("prompt") or "")
        prompts.append(prompt)
        if "workflow data auditor mode" in prompt and '"id": "task_001"' in prompt:
            return {"return_code": 0, "assistant_text": "no map block", "stderr": ""}
        if "workflow data auditor mode" in prompt and '"id": "task_002"' in prompt:
            payload = _task_map_for_task("task_002", "tables/results.csv")
            return {
                "return_code": 0,
                "assistant_text": "<task_data_map>"
                + json.dumps(payload)
                + "</task_data_map>",
                "stderr": "",
            }
        return {
            "return_code": 0,
            "assistant_text": "<reproduce_plan>"
            + json.dumps(planner_plan)
            + "</reproduce_plan>",
            "stderr": "",
        }

    monkeypatch.setattr(workflow_commands, "_run_reproduce_exec_turn", fake_exec_turn)
    data_context = _data_context(
        source_data_dir=source_data_dir,
        run_dir=run_dir,
        mapping_thresholds={
            "large_threshold_files": 1,
            "large_threshold_chars": 1,
            "task_slice_max_files": 120,
            "task_slice_max_chars": 45000,
        },
    )
    plan = cli._generate_reproduce_plan(
        repo_dir=repo_dir,
        run_dir=run_dir,
        source_text="source",
        source_description="paper.md",
        requested_package_id=None,
        sandbox_override=None,
        codex_bin="codex",
        planner_max_tries=1,
        auditor_max_tries=1,
        data_context=data_context,
    )
    assert plan["version"] == 1
    data_prompts = [p for p in prompts if "workflow data auditor mode" in p]
    assert len(data_prompts) == 2

    task_map = json.loads((data_root / "task_data_map.json").read_text(encoding="utf-8"))
    task_entries = {
        str(item.get("id")): item for item in task_map.get("tasks", []) if isinstance(item, dict)
    }
    assert "task_001" in task_entries
    assert "task_002" in task_entries
    assert "fallback" in " ".join(task_entries["task_001"].get("notes", [])).lower()
    task_002_files = task_entries["task_002"].get("files")
    assert isinstance(task_002_files, list)
    assert task_002_files
    assert task_002_files[0]["path"] == "tables/results.csv"


def test_data_context_compatibility_rejects_filter_setting_mismatch() -> None:
    base_context = {
        "enabled": True,
        "source_path": "/tmp/data",
        "read_only": True,
        "limits": _scan_limits(),
        "filter_settings": workflow_commands._default_data_filter_settings(),
        "mapping_thresholds": workflow_commands._default_data_mapping_thresholds(),
    }
    state_context = dict(base_context)
    invocation_context = dict(base_context)
    invocation_context["filter_settings"] = dict(
        workflow_commands._default_data_filter_settings()
    )
    invocation_context["filter_settings"]["drop_slurm_outputs"] = False

    with pytest.raises(cli.PackageError, match="filtering settings"):
        workflow_commands._assert_data_context_compatible(
            run_id="run-001",
            workflow_name="reproduce",
            state_data_context=state_context,
            invocation_data_context=invocation_context,
        )


def test_data_context_compatibility_rejects_mapping_threshold_mismatch() -> None:
    base_context = {
        "enabled": True,
        "source_path": "/tmp/data",
        "read_only": True,
        "limits": _scan_limits(),
        "filter_settings": workflow_commands._default_data_filter_settings(),
        "mapping_thresholds": workflow_commands._default_data_mapping_thresholds(),
    }
    state_context = dict(base_context)
    invocation_context = dict(base_context)
    invocation_context["mapping_thresholds"] = dict(
        workflow_commands._default_data_mapping_thresholds()
    )
    invocation_context["mapping_thresholds"]["large_threshold_files"] = 42

    with pytest.raises(cli.PackageError, match="mapping thresholds"):
        workflow_commands._assert_data_context_compatible(
            run_id="run-001",
            workflow_name="reproduce",
            state_data_context=state_context,
            invocation_data_context=invocation_context,
        )
