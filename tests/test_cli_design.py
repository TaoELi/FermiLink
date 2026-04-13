from __future__ import annotations

import importlib
from pathlib import Path

from fermilink import cli


def test_cli_design_invokes_design_pipeline(monkeypatch, tmp_path: Path, capsys) -> None:
    goal_path = tmp_path / "goal.md"
    goal_path.write_text("## Package\npyscf\n## Scientific Problem\nfoo\n", encoding="utf-8")
    project_root = tmp_path / "repo"
    project_root.mkdir()

    captured: dict[str, object] = {}

    def fake_run_pipeline(args):
        captured["goal_path"] = getattr(args, "goal_path", None)
        captured["project_root"] = getattr(args, "project_root", None)
        captured["provider"] = getattr(args, "provider", None)
        captured["search_profile"] = getattr(args, "search_profile", None)
        captured["baseline_publications"] = getattr(args, "baseline_publications", None)
        return {
            "shortlist_report_path": "/tmp/shortlist.md",
            "baseline_report_path": "/tmp/baseline.md",
            "archive_jsonl_path": "/tmp/archive.jsonl",
            "search_profile": getattr(args, "search_profile", None),
            "baseline_publications": getattr(args, "baseline_publications", None),
            "shortlist": [
                {
                    "rank": 1,
                    "score": 0.75,
                    "sketch": {"title": "Low-rank DIIS"},
                    "novelty": {"label": "plausible_novel_composition"},
                }
            ],
        }

    design_main = importlib.import_module("fermilink.design.main")
    monkeypatch.setattr(design_main, "run_pipeline", fake_run_pipeline)

    code = cli.main(
        [
            "design",
            str(goal_path),
            "--project-root",
            str(project_root),
            "--provider",
            "codex",
            "--search-profile",
            "novelty-seeking",
            "--baseline-publications",
        ]
    )

    assert code == 0
    assert captured["goal_path"] == str(goal_path)
    assert captured["project_root"] == str(project_root)
    assert captured["provider"] == "codex"
    assert captured["search_profile"] == "novelty-seeking"
    assert captured["baseline_publications"] is True
    out = capsys.readouterr().out
    assert "shortlist report" in out
    assert "search profile: novelty-seeking" in out
    assert "baseline publications: True" in out
    assert "Low-rank DIIS" in out


def test_cli_design_supports_json_output(monkeypatch, tmp_path: Path, capsys) -> None:
    goal_path = tmp_path / "goal.md"
    goal_path.write_text("## Package\npyscf\n## Scientific Problem\nfoo\n", encoding="utf-8")

    design_main = importlib.import_module("fermilink.design.main")
    monkeypatch.setattr(
        design_main,
        "run_pipeline",
        lambda args: {
            "shortlist_report_path": "/tmp/shortlist.md",
            "baseline_report_path": "/tmp/baseline.md",
            "archive_jsonl_path": "/tmp/archive.jsonl",
            "search_profile": getattr(args, "search_profile", None),
            "baseline_publications": getattr(args, "baseline_publications", None),
            "shortlist": [],
        },
    )

    code = cli.main(["design", str(goal_path), "--json"])

    assert code == 0
    out = capsys.readouterr().out
    assert '"shortlist_report_path": "/tmp/shortlist.md"' in out


def test_cli_design_defaults_search_profile_to_balanced(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    goal_path = tmp_path / "goal.md"
    goal_path.write_text("## Package\npyscf\n## Scientific Problem\nfoo\n", encoding="utf-8")

    captured: dict[str, object] = {}

    def fake_run_pipeline(args):
        captured["search_profile"] = getattr(args, "search_profile", None)
        captured["baseline_publications"] = getattr(args, "baseline_publications", None)
        return {
            "shortlist_report_path": "/tmp/shortlist.md",
            "baseline_report_path": "/tmp/baseline.md",
            "archive_jsonl_path": "/tmp/archive.jsonl",
            "search_profile": getattr(args, "search_profile", None),
            "baseline_publications": getattr(args, "baseline_publications", None),
            "shortlist": [],
        }

    design_main = importlib.import_module("fermilink.design.main")
    monkeypatch.setattr(design_main, "run_pipeline", fake_run_pipeline)

    code = cli.main(["design", str(goal_path)])

    assert code == 0
    assert captured["search_profile"] == "balanced"
    assert captured["baseline_publications"] is False
    out = capsys.readouterr().out
    assert "search profile: balanced" in out
    assert "baseline publications: False" in out
