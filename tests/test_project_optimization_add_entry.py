from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADD_ENTRY_PATH = ROOT / "project-optimization" / "scripts" / "add_entry.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "test_project_optimization_add_entry_module",
        ADD_ENTRY_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_add_entry_forwards_git_push_flag(monkeypatch, tmp_path: Path) -> None:
    add_entry = _load_module()

    optimize_dir = tmp_path / ".fermilink-optimize"
    optimize_dir.mkdir(parents=True)
    (optimize_dir / "results.tsv").write_text(
        (
            "iteration\tcommit\tstatus\tprimary_metric_name\tprimary_metric_value\tdescription\n"
            "0\tbaseline123456\tbaseline\twall_seconds\t10.0\tbaseline\n"
        ),
        encoding="utf-8",
    )

    fake_builder = tmp_path / "build_report.py"
    fake_builder.write_text("print('stub')\n", encoding="utf-8")
    monkeypatch.setattr(add_entry, "SKILL_BUILDER", fake_builder)
    monkeypatch.setattr(add_entry, "ENTRIES_ROOT", tmp_path / "entries")

    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], check: bool = False
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(add_entry.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "add_entry.py",
            str(optimize_dir),
            "--package",
            "mockpkg",
            "--task",
            "feature",
            "--git-push",
        ],
    )

    add_entry.main()

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[:2] == [sys.executable, str(fake_builder)]
    assert cmd[2:] == [
        str(optimize_dir),
        "--out",
        str((tmp_path / "entries" / "mockpkg" / "feature")),
        "--git-push",
    ]
