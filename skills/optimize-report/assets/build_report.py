"""Build a Sphinx-ready report bundle from a .fermilink-optimize directory.

Usage:
    python build_report.py <optimize-dir> --out <report-dir> [--title TITLE]
                           [--metric-label LABEL] [--direction lower|higher]

Produces:
    <report-dir>/
        index.rst
        img/metric_vs_iter.{png,svg}
        img/improvement_cumulative.{png,svg}
        iterations/iter_XXXX_accepted.rst
        contract/{benchmark.yaml, benchmark_runner.py, goal.md, goal_inputs.json, ...}
        data/{results.tsv, summary.json}
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Optional

ASSETS_DIR = Path(__file__).resolve().parent
if str(ASSETS_DIR) not in sys.path:
    sys.path.insert(0, str(ASSETS_DIR))

import plot_optimize  # noqa: E402


def split_description(desc: str) -> tuple[str, str]:
    """Split the `description` field into (change_summary, rationale).

    The rationale is the trailing bracketed tail ``[...]``. Descriptions can
    contain unrelated brackets like ``hneigh[][2]`` or ``x[j]`` mid-sentence,
    so we scan backward from the final ``]`` with a depth counter to find the
    matching opening bracket.
    """
    desc = (desc or "").strip()
    if not desc.endswith("]"):
        return desc, ""
    depth = 0
    for i in range(len(desc) - 1, -1, -1):
        c = desc[i]
        if c == "]":
            depth += 1
        elif c == "[":
            depth -= 1
            if depth == 0:
                return desc[:i].rstrip(), desc[i + 1 : -1].strip()
    return desc, ""


def rst_escape(text: str) -> str:
    return (text or "").replace("|", "\\|")


def rst_title(text: str, char: str) -> str:
    return f"{text}\n{char * max(len(text), 3)}\n"


def copy_tree(src: Path, dst: Path) -> None:
    if src.exists():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def copy_contract(optimize_dir: Path, out_dir: Path) -> list[str]:
    dst = out_dir / "contract"
    dst.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    autogen = optimize_dir / "autogen"
    if autogen.exists():
        for name in (
            "benchmark.yaml",
            "benchmark_runner.py",
            "goal.md",
            "goal_inputs.json",
            "goal_analysis.json",
            "goal_mode.json",
            "run_optimize.sh",
            "setup_env.sh",
        ):
            src = autogen / name
            if src.exists():
                shutil.copy2(src, dst / name)
                copied.append(name)
    for name in ("program.md", "memory.md", "worker_memory.md"):
        src = optimize_dir / name
        if src.exists():
            shutil.copy2(src, dst / name)
            copied.append(name)
    return copied


def load_review_context(iter_dir: Path) -> dict:
    p = iter_dir / "review_context.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def load_controller_result(iter_dir: Path) -> dict:
    p = iter_dir / "controller_result.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def read_text_safe(path: Path, limit: Optional[int] = None) -> str:
    if not path.exists():
        return ""
    try:
        data = path.read_text(errors="replace")
    except OSError:
        return ""
    if limit and len(data) > limit:
        return data[:limit] + f"\n... (truncated, full file {len(data)} bytes)"
    return data


def build_guardrails_table(review: dict, controller: dict) -> str:
    """Return an RST grid table summarizing guardrail fields."""
    if not review and not controller:
        return ""

    rows: list[tuple[str, str]] = []
    decision = controller.get("decision") or ""
    if decision:
        rows.append(("decision", decision))
    correctness = review.get("correctness", {}) or {}
    rows.append(("correctness", "ok" if correctness.get("ok") else "failed"))
    rows.append(("correctness mode", str(correctness.get("mode") or "")))
    rows.append(("hard reject", "yes" if review.get("hard_reject") else "no"))
    if review.get("hard_reject_reason"):
        rows.append(("reject reason", str(review["hard_reject_reason"])))
    guardrail_errors = (
        (review.get("candidate_metrics") or {}).get("guardrail_errors") or []
    )
    rows.append(("guardrail errors", str(len(guardrail_errors))))
    rows.append(("incumbent commit", str(review.get("incumbent_commit", ""))[:12]))
    rows.append(("candidate commit", str(review.get("candidate_commit", ""))[:12]))
    inc_metric = review.get("incumbent_primary_metric")
    cand_metric = review.get("candidate_primary_metric")
    base_metric = review.get("baseline_primary_metric")
    if inc_metric is not None:
        rows.append(("incumbent metric", f"{float(inc_metric):.6g}"))
    if cand_metric is not None:
        rows.append(("candidate metric", f"{float(cand_metric):.6g}"))
    if base_metric is not None:
        rows.append(("baseline metric", f"{float(base_metric):.6g}"))
    if inc_metric and cand_metric:
        delta = (float(inc_metric) - float(cand_metric)) / float(inc_metric) * 100
        rows.append(("Δ vs incumbent", f"{delta:+.3f}% (lower-is-better sign)"))
    changed = review.get("editable_changed_paths") or review.get("changed_paths") or []
    if changed:
        rows.append(("changed files", ", ".join(changed[:6]) + (" …" if len(changed) > 6 else "")))

    key_w = max(len("field"), max(len(k) for k, _ in rows))
    val_w = max(len("value"), max(len(v) for _, v in rows))
    border = f"+{'-' * (key_w + 2)}+{'-' * (val_w + 2)}+"
    header_sep = f"+{'=' * (key_w + 2)}+{'=' * (val_w + 2)}+"
    out = [border, f"| {'field'.ljust(key_w)} | {'value'.ljust(val_w)} |", header_sep]
    for k, v in rows:
        out.append(f"| {k.ljust(key_w)} | {v.ljust(val_w)} |")
        out.append(border)
    return "\n".join(out) + "\n"


def build_index(
    out_dir: Path,
    title: str,
    rows: list[plot_optimize.Row],
    direction: str,
    metric_label: str,
    accepted_pages: list[tuple[int, str]],
    contract_files: list[str],
) -> None:
    baseline = next((r for r in rows if r.status == "baseline"), None)
    accepted = [r for r in rows if r.status == "accepted"]
    best = accepted[-1] if accepted else baseline
    total = len(rows)
    n_accepted = len(accepted)
    n_rejected = sum(1 for r in rows if r.status == "rejected")
    n_failure = sum(1 for r in rows if r.status == "correctness_failure")

    if baseline and best and best is not baseline:
        if direction == "lower":
            pct = (baseline.metric_value - best.metric_value) / baseline.metric_value * 100
        else:
            pct = (best.metric_value - baseline.metric_value) / baseline.metric_value * 100
    else:
        pct = 0.0

    lines: list[str] = []
    lines.append(rst_title(title, "="))
    lines.append("")
    lines.append(f"Primary metric: ``{metric_label}`` ({'lower is better' if direction == 'lower' else 'higher is better'}).")
    lines.append("")
    lines.append(rst_title("Summary", "-"))
    lines.append("")
    if baseline:
        lines.append(f"- baseline (``{baseline.commit}``): ``{baseline.metric_value:.6g}``")
    if best and best is not baseline:
        lines.append(f"- best accepted (``{best.commit}``): ``{best.metric_value:.6g}`` ({pct:+.2f}% vs baseline)")
    lines.append(f"- iterations: {total} total | {n_accepted} accepted | {n_rejected} rejected | {n_failure} correctness failure")
    lines.append("")
    lines.append(rst_title("Trajectory", "-"))
    lines.append("")
    lines.append(".. image:: img/metric_vs_iter.svg")
    lines.append("   :width: 100%")
    lines.append("   :alt: metric vs iteration")
    lines.append("")
    lines.append(".. image:: img/improvement_cumulative.svg")
    lines.append("   :width: 100%")
    lines.append("   :alt: running incumbent")
    lines.append("")
    lines.append(rst_title("All iterations", "-"))
    lines.append("")

    header = ("iter", "commit", "status", "metric", "summary")
    table_rows: list[tuple[str, ...]] = [header]
    for r in rows:
        summary, _ = split_description(r.description)
        if len(summary) > 100:
            summary = summary[:97] + "…"
        table_rows.append(
            (
                str(r.iteration),
                r.commit,
                r.status,
                f"{r.metric_value:.6g}",
                rst_escape(summary),
            )
        )
    widths = [max(len(row[i]) for row in table_rows) for i in range(len(header))]
    border = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    sep = "+" + "+".join("=" * (w + 2) for w in widths) + "+"
    lines.append(border)
    lines.append("| " + " | ".join(table_rows[0][i].ljust(widths[i]) for i in range(len(header))) + " |")
    lines.append(sep)
    for row in table_rows[1:]:
        lines.append("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(header))) + " |")
        lines.append(border)
    lines.append("")

    if accepted_pages:
        lines.append(rst_title("Accepted commits — detailed", "-"))
        lines.append("")
        lines.append(".. toctree::")
        lines.append("   :maxdepth: 1")
        lines.append("")
        for _, rel in accepted_pages:
            lines.append(f"   {rel}")
        lines.append("")

    if contract_files:
        lines.append(rst_title("Contract", "-"))
        lines.append("")
        lines.append("Benchmark contract and runner used for this optimization:")
        lines.append("")
        for name in contract_files:
            lines.append(f"- :download:`{name} <contract/{name}>`")
        lines.append("")

    lines.append(rst_title("Data", "-"))
    lines.append("")
    lines.append("- :download:`results.tsv <data/results.tsv>`")
    lines.append("- :download:`summary.json <data/summary.json>`")
    lines.append("")

    (out_dir / "index.rst").write_text("\n".join(lines))


def build_iter_page(
    out_dir: Path,
    row: plot_optimize.Row,
    iter_src: Path,
) -> Optional[str]:
    if row.status != "accepted":
        return None

    pages_dir = out_dir / "iterations"
    diffs_dir = pages_dir / "_diffs"
    pages_dir.mkdir(parents=True, exist_ok=True)
    diffs_dir.mkdir(parents=True, exist_ok=True)

    review = load_review_context(iter_src)
    controller = load_controller_result(iter_src)
    summary, rationale = split_description(row.description)

    diff_name = f"iter_{row.iteration:04d}_{row.commit}.diff"
    diffstat_text = read_text_safe(iter_src / "candidate_diff_stat.txt")
    diff_text = read_text_safe(iter_src / "candidate.diff")
    if diff_text:
        (diffs_dir / diff_name).write_text(diff_text)

    page_stem = f"iter_{row.iteration:04d}_accepted"
    page_path = pages_dir / f"{page_stem}.rst"

    title = f"Iteration {row.iteration:04d} — {row.commit} (accepted)"
    lines: list[str] = [rst_title(title, "="), ""]

    lines.append(rst_title("Change summary", "-"))
    lines.append("")
    lines.append(summary or "(no summary)")
    lines.append("")

    if rationale:
        lines.append(rst_title("Acceptance rationale", "-"))
        lines.append("")
        lines.append(rationale)
        lines.append("")

    table = build_guardrails_table(review, controller)
    if table:
        lines.append(rst_title("Guardrails & metrics", "-"))
        lines.append("")
        lines.append(table)
        lines.append("")

    if diffstat_text.strip():
        lines.append(rst_title("Diffstat", "-"))
        lines.append("")
        lines.append(".. code-block:: text")
        lines.append("")
        for ln in diffstat_text.splitlines():
            lines.append(f"   {ln}")
        lines.append("")

    if diff_text:
        lines.append(rst_title("Diff", "-"))
        lines.append("")
        lines.append(f":download:`download full diff <_diffs/{diff_name}>`")
        lines.append("")
        lines.append(".. code-block:: diff")
        lines.append("")
        shown = diff_text.splitlines()
        cap = 600
        for ln in shown[:cap]:
            lines.append(f"   {ln}")
        if len(shown) > cap:
            lines.append(f"   ... ({len(shown) - cap} more lines; see download link above)")
        lines.append("")

    page_path.write_text("\n".join(lines))
    return f"iterations/{page_stem}"


def build_summary_json(rows: list[plot_optimize.Row], direction: str, metric_label: str) -> dict:
    baseline = next((r for r in rows if r.status == "baseline"), None)
    accepted = [r for r in rows if r.status == "accepted"]
    best = accepted[-1] if accepted else baseline
    pct = 0.0
    if baseline and best and best is not baseline:
        if direction == "lower":
            pct = (baseline.metric_value - best.metric_value) / baseline.metric_value * 100
        else:
            pct = (best.metric_value - baseline.metric_value) / baseline.metric_value * 100
    return {
        "metric_label": metric_label,
        "direction": direction,
        "iterations_total": len(rows),
        "accepted": [r.commit for r in accepted],
        "rejected": [r.commit for r in rows if r.status == "rejected"],
        "correctness_failure": [r.commit for r in rows if r.status == "correctness_failure"],
        "baseline": {
            "commit": baseline.commit if baseline else None,
            "metric": baseline.metric_value if baseline else None,
        },
        "best": {
            "commit": best.commit if best else None,
            "metric": best.metric_value if best else None,
            "pct_vs_baseline": pct,
        },
    }


def build(
    optimize_dir: Path,
    out_dir: Path,
    title: Optional[str] = None,
    metric_label: Optional[str] = None,
    direction: Optional[str] = None,
) -> Path:
    optimize_dir = optimize_dir.resolve()
    out_dir = out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    results_tsv = optimize_dir / "results.tsv"
    if not results_tsv.exists():
        raise SystemExit(f"results.tsv not found under {optimize_dir}")

    rows = plot_optimize.load_results(results_tsv)
    if not rows:
        raise SystemExit(f"no parseable rows in {results_tsv}")
    dir_final = direction or plot_optimize.infer_direction(rows)
    label = metric_label or plot_optimize.humanize_metric_label(rows[0].metric_name if rows else "")
    final_title = title or f"Optimization Report — {optimize_dir.parent.name}"

    (out_dir / "img").mkdir()
    plot_optimize.render(results_tsv, out_dir / "img", metric_label=label, direction=dir_final)

    (out_dir / "data").mkdir()
    shutil.copy2(results_tsv, out_dir / "data" / "results.tsv")
    summary = build_summary_json(rows, dir_final, label)
    (out_dir / "data" / "summary.json").write_text(json.dumps(summary, indent=2))

    contract_files = copy_contract(optimize_dir, out_dir)

    accepted_pages: list[tuple[int, str]] = []
    runs_dir = optimize_dir / "runs"
    for r in rows:
        if r.status != "accepted":
            continue
        iter_src = runs_dir / f"iter_{r.iteration:04d}"
        rel = build_iter_page(out_dir, r, iter_src)
        if rel:
            accepted_pages.append((r.iteration, rel))

    build_index(out_dir, final_title, rows, dir_final, label, accepted_pages, contract_files)
    return out_dir


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("optimize_dir", type=Path, help="path to a .fermilink-optimize directory")
    ap.add_argument("--out", type=Path, default=None, help="output directory (default: <optimize-dir>/../optimize-report)")
    ap.add_argument("--title", default=None)
    ap.add_argument("--metric-label", default=None)
    ap.add_argument("--direction", choices=["lower", "higher"], default=None)
    args = ap.parse_args()

    out = args.out or (args.optimize_dir.resolve().parent / "optimize-report")
    path = build(
        optimize_dir=args.optimize_dir,
        out_dir=out,
        title=args.title,
        metric_label=args.metric_label,
        direction=args.direction,
    )
    print(f"wrote report bundle to {path}")


if __name__ == "__main__":
    main()
