import argparse
import random
import sys

import matplotlib.pyplot as plt
import pandas as pd

from src.config import OUTPUTS_DIR

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

IN_CSV = OUTPUTS_DIR / "exp1_results.csv"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample-hists", type=int, default=10)
    ap.add_argument("--bins", type=int, default=25)
    args = ap.parse_args()

    raw = pd.read_csv(IN_CSV, encoding="latin-1")
    all_names = sorted(raw["input_name"].unique())
    scored = raw[raw["call_status"].isin(["ok", "ok_truncated"])].copy()
    scored["score"] = pd.to_numeric(scored["score"], errors="coerce")
    scored = scored.dropna(subset=["score"])

    print(f"Loaded {len(raw)} rows, {len(all_names)} names")
    print(f"  scored rows: {len(scored)}")
    print(f"  empty rows:  {(raw['call_status']=='ok_empty').sum()}\n")

    summary = (
        scored.groupby("input_name")["score"]
        .agg(max_score="max", n_results="count")
        .round(4)
        .reset_index()
    )
    zero_names = sorted(set(all_names) - set(summary["input_name"]))
    if zero_names:
        zero_df = pd.DataFrame(
            {"input_name": zero_names, "max_score": 0.0, "n_results": 0}
        )
        summary = pd.concat([summary, zero_df], ignore_index=True)
    summary = summary.sort_values("max_score", ascending=False).reset_index(drop=True)
    summary.insert(0, "rank", range(1, len(summary) + 1))
    summary.to_csv(OUTPUTS_DIR / "exp1_per_name_summary.csv", index=False)
    print(f"-> exp1_per_name_summary.csv ({len(summary)} names, ranked 1-{len(summary)})")

    all_scores = scored["score"].values
    n_with_matches = scored["input_name"].nunique()
    n_zero_match = len(all_names) - n_with_matches
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.hist(all_scores, bins=40, edgecolor="black", alpha=0.75)
    ax.set_xlim(0, 1)
    ax.set_xlabel("Distance Score")
    ax.set_ylabel("Number of trademark matches")
    ax.set_title(
        f"Distribution of {len(all_scores)} match scores from "
        f"{len(all_names)} candidate names "
        f"({n_zero_match} had no matches, not shown)"
    )
    label_lines = []
    for t in (0.7, 0.8, 0.9):
        n_above = (all_scores >= t).sum()
        pct = n_above / len(all_scores) * 100
        ax.axvline(t, color="crimson", linestyle="--", linewidth=1, alpha=0.7)
        label_lines.append(f"T = {t}: {n_above} matches above ({pct:.0f}%)")
    ax.text(
        0.02,
        0.98,
        "\n\n".join(label_lines),
        transform=ax.transAxes,
        va="top",
        ha="left",
        fontsize=10,
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor="white",
            edgecolor="crimson",
            alpha=0.9,
        ),
    )
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUTS_DIR / "exp1_global_histogram.png", dpi=120)
    plt.close(fig)
    print("-> exp1_global_histogram.png")

    max_scores = summary["max_score"].values
    fig, ax = plt.subplots(figsize=(11, 5))
    ax.hist(max_scores, bins=20, edgecolor="black", alpha=0.75, color="seagreen")
    ax.set_xlim(0, 1)
    ax.set_xlabel("Max distance score per name")
    ax.set_ylabel("Number of names")
    n_zero = (max_scores == 0).sum()
    n_high = (max_scores >= 0.9).sum()
    ax.set_title(
        f"Highest similarity per candidate ({len(max_scores)} candidates total): "
        f"{n_zero} have max=0, {n_high} have max>=0.9"
    )
    for t in (0.7, 0.8, 0.9):
        ax.axvline(t, color="crimson", linestyle="--", linewidth=1, alpha=0.7)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUTS_DIR / "exp1_max_score_per_name.png", dpi=120)
    plt.close(fig)
    print("-> exp1_max_score_per_name.png")

    out_dir = OUTPUTS_DIR / "exp1_sample_hists"
    out_dir.mkdir(exist_ok=True)
    for p in out_dir.glob("*.png"):
        p.unlink()

    matched = sorted(scored["input_name"].unique())
    sample = random.Random(42).sample(matched, min(args.sample_hists, len(matched)))
    for name in sample:
        scores = scored.loc[scored["input_name"] == name, "score"]
        fig, ax = plt.subplots(figsize=(7, 4))
        ax.hist(scores, bins=args.bins, edgecolor="black", alpha=0.75)
        ax.set_xlim(0, 1)
        ax.set_xlabel("Distance Score")
        ax.set_title(f"{name} - {len(scores)} matches, max={scores.max():.3f}")
        fig.tight_layout()
        fig.savefig(out_dir / f"{name}.png", dpi=120)
        plt.close(fig)
    print(f"-> exp1_sample_hists/ ({len(sample)} histograms)")


if __name__ == "__main__":
    main()
