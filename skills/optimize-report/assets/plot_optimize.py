"""Plot fermilink optimize trajectories from a results.tsv file.

Usage:
    python plot_optimize.py <optimize-dir> --out-dir <img-dir> [options]

Produces metric_vs_iter.{png,svg} and improvement_cumulative.{png,svg}.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


STATUS_STYLE = {
    "baseline": {"color": "#6b7280", "marker": "s", "label": "baseline"},
    "accepted": {"color": "#16a34a", "marker": "o", "label": "accepted"},
    "rejected": {"color": "#f59e0b", "marker": "x", "label": "rejected"},
    "correctness_failure": {"color": "#dc2626", "marker": "X", "label": "correctness failure"},
}


@dataclass
class Row:
    iteration: int
    commit: str
    status: str
    metric_name: str
    metric_value: float
    description: str


PREFIX_LABELS = {
    "weighted_median": "Weighted median",
    "geomean": "Geometric mean",
    "median": "Median",
    "mean": "Mean",
    "peak": "Peak",
    "total": "Total",
    "max": "Max",
    "min": "Min",
}

TOKEN_LABELS = {
    "cpu": "CPU",
    "gpu": "GPU",
    "rss": "RSS",
    "scf": "SCF",
}

STEM_LABELS = {
    "wall": "wall time",
    "wall_ratio": "wall-time ratio",
    "cpu": "CPU time",
}


def _extract_metric_prefix(metric_name: str) -> tuple[str, str]:
    for prefix, label in sorted(PREFIX_LABELS.items(), key=lambda item: len(item[0]), reverse=True):
        token = prefix + "_"
        if metric_name.startswith(token):
            return label, metric_name[len(token) :]
        if metric_name == prefix:
            return label, ""
    return "", metric_name


def _humanize_tokens(text: str, *, capitalize: bool) -> str:
    parts = [TOKEN_LABELS.get(token, token) for token in text.split("_") if token]
    if not parts:
        return ""
    phrase = " ".join(parts)
    if capitalize and phrase and not phrase[0].isupper():
        phrase = phrase[0].upper() + phrase[1:]
    return phrase


def humanize_metric_label(metric_name: str) -> str:
    metric_name = (metric_name or "").strip()
    if not metric_name:
        return "Primary metric"

    prefix, stem = _extract_metric_prefix(metric_name)
    working = stem or metric_name

    per_phrase = ""
    if "_per_" in working:
        working, per_suffix = working.split("_per_", 1)
        per_phrase = f" per {_humanize_tokens(per_suffix, capitalize=False)}"

    comparison_phrase = ""
    if "_vs_" in working:
        working, comparison_suffix = working.split("_vs_", 1)
        comparison_phrase = f" vs {_humanize_tokens(comparison_suffix, capitalize=False)}"

    unit = ""
    is_seconds_metric = False
    if working.endswith("_seconds"):
        working = working[: -len("_seconds")]
        is_seconds_metric = True
        unit = " (s)"
    elif working.endswith("_mb"):
        working = working[: -len("_mb")]
        unit = " (MB)"

    phrase = STEM_LABELS.get(working)
    if phrase is None:
        phrase = _humanize_tokens(working, capitalize=not bool(prefix))
    if not phrase:
        phrase = "primary metric"
    if is_seconds_metric and "time" not in phrase.lower():
        phrase = f"{phrase} time"

    label = f"{prefix} {phrase}".strip()
    return f"{label}{per_phrase}{comparison_phrase}{unit}"


def load_results(path: Path) -> list[Row]:
    rows: list[Row] = []
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for r in reader:
            try:
                value = float(r["primary_metric_value"])
            except (TypeError, ValueError):
                continue
            rows.append(
                Row(
                    iteration=int(r["iteration"]),
                    commit=(r.get("commit") or "")[:12],
                    status=(r.get("status") or "").strip(),
                    metric_name=r.get("primary_metric_name") or "",
                    metric_value=value,
                    description=r.get("description") or "",
                )
            )
    rows.sort(key=lambda x: x.iteration)
    return rows


def infer_direction(rows: list[Row]) -> str:
    """Return 'lower' if lower is better, 'higher' otherwise.

    Heuristic: compare the last accepted/baseline value to the baseline; if it
    dropped, lower-is-better.
    """
    baseline = next((r for r in rows if r.status == "baseline"), None)
    if baseline is None:
        return "lower"
    accepted = [r for r in rows if r.status == "accepted"]
    if not accepted:
        return "lower"
    return "lower" if accepted[-1].metric_value <= baseline.metric_value else "higher"


def running_best(rows: list[Row], direction: str) -> list[tuple[int, float, str]]:
    """Return per-iter incumbent (iteration, best_value, commit) series."""
    out: list[tuple[int, float, str]] = []
    best_val: Optional[float] = None
    best_commit = ""
    better = (lambda a, b: a < b) if direction == "lower" else (lambda a, b: a > b)
    for r in rows:
        if r.status == "baseline" or r.status == "accepted":
            if best_val is None or better(r.metric_value, best_val):
                best_val = r.metric_value
                best_commit = r.commit
        if best_val is not None:
            out.append((r.iteration, best_val, best_commit))
    return out


def _style_axes(ax, title: str, ylabel: str) -> None:
    ax.set_title(title, fontsize=12, fontweight="bold")
    ax.set_xlabel("iteration")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.3, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


def plot_metric_vs_iter(
    rows: list[Row],
    out_dir: Path,
    metric_label: str,
    direction: str,
) -> Path:
    fig, ax = plt.subplots(figsize=(9, 5))
    iters = [r.iteration for r in rows]
    vals = [r.metric_value for r in rows]
    ax.plot(iters, vals, color="#9ca3af", linewidth=1.0, alpha=0.6, zorder=1)

    seen_labels: set[str] = set()
    for r in rows:
        style = STATUS_STYLE.get(r.status, {"color": "#374151", "marker": "o", "label": r.status})
        label = style["label"] if style["label"] not in seen_labels else None
        if label:
            seen_labels.add(style["label"])
        ax.scatter(
            r.iteration,
            r.metric_value,
            color=style["color"],
            marker=style["marker"],
            s=70,
            zorder=3,
            label=label,
            edgecolors="white" if style["marker"] in {"o", "s", "X"} else None,
            linewidths=0.8 if style["marker"] in {"o", "s", "X"} else 1.5,
        )
        if r.status == "accepted":
            ax.annotate(
                r.commit,
                xy=(r.iteration, r.metric_value),
                xytext=(4, 6),
                textcoords="offset points",
                fontsize=7,
                color="#065f46",
            )

    direction_note = "lower is better" if direction == "lower" else "higher is better"
    _style_axes(ax, f"Optimization trajectory — {direction_note}", metric_label)
    ax.legend(loc="best", frameon=False, fontsize=9)
    fig.tight_layout()

    png = out_dir / "metric_vs_iter.png"
    svg = out_dir / "metric_vs_iter.svg"
    fig.savefig(png, dpi=150)
    fig.savefig(svg)
    plt.close(fig)
    return png


def plot_running_best(
    rows: list[Row],
    out_dir: Path,
    metric_label: str,
    direction: str,
) -> Path:
    series = running_best(rows, direction)
    if not series:
        return out_dir / "improvement_cumulative.png"

    baseline = next((r for r in rows if r.status == "baseline"), None)
    baseline_val = baseline.metric_value if baseline else series[0][1]

    fig, ax = plt.subplots(figsize=(9, 5))
    xs = [s[0] for s in series]
    ys = [s[1] for s in series]
    ax.step(xs, ys, where="post", color="#16a34a", linewidth=2.0, label="incumbent")
    ax.axhline(baseline_val, color="#6b7280", linestyle=":", linewidth=1.2, label="baseline")

    # Secondary axis: percent improvement vs baseline.
    ax2 = ax.twinx()
    if direction == "lower":
        pct = [(baseline_val - y) / baseline_val * 100 for y in ys]
    else:
        pct = [(y - baseline_val) / baseline_val * 100 for y in ys]
    ax2.step(xs, pct, where="post", color="#2563eb", linewidth=1.2, alpha=0.0)
    ax2.set_ylabel("improvement vs baseline (%)", color="#2563eb")
    ax2.tick_params(axis="y", colors="#2563eb")
    ax2.spines["top"].set_visible(False)

    _style_axes(ax, "Running incumbent", metric_label)
    ax.legend(loc="best", frameon=False, fontsize=9)
    fig.tight_layout()

    png = out_dir / "improvement_cumulative.png"
    svg = out_dir / "improvement_cumulative.svg"
    fig.savefig(png, dpi=150)
    fig.savefig(svg)
    plt.close(fig)
    return png


def render(
    results_tsv: Path,
    out_dir: Path,
    metric_label: Optional[str] = None,
    direction: Optional[str] = None,
) -> dict:
    rows = load_results(results_tsv)
    if not rows:
        raise SystemExit(f"no rows parsed from {results_tsv}")
    out_dir.mkdir(parents=True, exist_ok=True)
    dir_final = direction or infer_direction(rows)
    label = metric_label or humanize_metric_label(rows[0].metric_name if rows else "")
    plot_metric_vs_iter(rows, out_dir, label, dir_final)
    plot_running_best(rows, out_dir, label, dir_final)
    return {
        "direction": dir_final,
        "metric_label": label,
        "row_count": len(rows),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("optimize_dir", type=Path, help="path to a .fermilink-optimize directory")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--metric-label", default=None)
    ap.add_argument(
        "--direction",
        choices=["lower", "higher"],
        default=None,
        help="lower-is-better vs higher-is-better; auto-detected if omitted",
    )
    args = ap.parse_args()
    results_tsv = args.optimize_dir / "results.tsv"
    info = render(results_tsv, args.out_dir, args.metric_label, args.direction)
    print(f"wrote plots to {args.out_dir} ({info})")


if __name__ == "__main__":
    main()
