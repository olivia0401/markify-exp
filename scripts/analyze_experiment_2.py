import json
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from src.config import OUTPUTS_DIR, RATE_LIMIT_SECONDS

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FULL_CSV = OUTPUTS_DIR / "exp2_full_results.csv"
VAR_CSV = OUTPUTS_DIR / "exp2_variance_results.csv"
VALID_STATUSES = ["ok", "split_ok", "split_partial"]
QUOTA_PER_PERIOD = 2000


def classify_error(err):
    if err is None or (isinstance(err, float) and pd.isna(err)):
        return "none"
    s = str(err)
    if "Quota" in s and "exceeded" in s:
        return "quota_exceeded"
    if "disallowed parameter" in s:
        return "bad_db_code"
    if s.strip() == "":
        return "none"
    return "other"


def main():
    df_full = pd.read_csv(FULL_CSV)
    n_total = len(df_full)
    print(f"Loaded {n_total} scenarios")

    df_full["error_type"] = df_full["original_error"].apply(classify_error)
    n_quota = (df_full["error_type"] == "quota_exceeded").sum()

    if n_quota > 0:
        first_q = df_full.loc[
            df_full["error_type"] == "quota_exceeded", "timestamp_utc"
        ].min()
        pre_q_mask = df_full["timestamp_utc"] < first_q
        print(f"Quota exhausted at {first_q}")
        print(f"  Pre-quota: {int(pre_q_mask.sum())}, Post-quota: {int((~pre_q_mask).sum())}")
        df = df_full[pre_q_mask].copy()
    else:
        df = df_full.copy()

    valid = df[df["attempt_status"].isin(VALID_STATUSES)].copy()
    n_valid = len(valid)
    print(f"Valid for timing: {n_valid} / {len(df)}\n")

    ok_data = valid[valid["attempt_status"] == "ok"]["total_elapsed"]
    split_data = valid[
        valid["attempt_status"].isin(["split_ok", "split_partial"])
    ]["total_elapsed"]
    ok_mean = ok_data.mean() if len(ok_data) else 0
    split_mean = split_data.mean() if len(split_data) else 0

    if n_valid > 0:
        bucket = (
            valid.groupby("db_count")
            .agg(
                n_scenarios=("total_elapsed", "count"),
                mean_seconds=("total_elapsed", "mean"),
                median_seconds=("total_elapsed", "median"),
                p95_seconds=("total_elapsed", lambda s: s.quantile(0.95)),
                max_seconds=("total_elapsed", "max"),
            )
            .round(2)
            .reset_index()
        )
        bucket.to_csv(OUTPUTS_DIR / "exp2_latency_by_db_count.csv", index=False)
        print("-> exp2_latency_by_db_count.csv")

    fig, ax = plt.subplots(figsize=(11, 5))
    bins = np.linspace(0, valid["total_elapsed"].max() if n_valid else 10, 40)
    if len(ok_data):
        ax.hist(
            ok_data,
            bins=bins,
            alpha=0.6,
            color="steelblue",
            label=f"Single call (n={len(ok_data)}, avg {ok_mean:.1f}s)",
        )
        ax.axvline(ok_mean, color="steelblue", linestyle="--", linewidth=1)
    if len(split_data):
        ax.hist(
            split_data,
            bins=bins,
            alpha=0.6,
            color="orange",
            label=f"Split per-DB (n={len(split_data)}, avg {split_mean:.1f}s)",
        )
        ax.axvline(split_mean, color="orange", linestyle="--", linewidth=1)
    ax.set_xlabel("Time per scenario (seconds)")
    ax.set_ylabel("Number of scenarios")
    ax.set_title(f"Latency distribution: {n_valid} valid scenarios")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUTS_DIR / "exp2_latency_distribution.png", dpi=120)
    plt.close(fig)
    print("-> exp2_latency_distribution.png")

    var_df = None
    if VAR_CSV.exists():
        var_df_full = pd.read_csv(VAR_CSV).sort_values(["scenario_id", "round_idx"])
        var_df = var_df_full[var_df_full["original_status"] == "ok"].copy()
        if len(var_df) == 0:
            var_df = None
        else:
            fig, ax = plt.subplots(figsize=(11, 5))
            for sid, group in var_df.groupby("scenario_id"):
                running_mean = group["total_elapsed"].expanding().mean()
                x = list(range(1, len(group) + 1))
                db = group["db_count"].iloc[0]
                ax.plot(
                    x,
                    running_mean,
                    marker="o",
                    markersize=3,
                    label=f"sid={sid} ({db} DBs, n={len(group)})",
                )
            ax.set_xlabel("Round")
            ax.set_ylabel("Running mean of total_elapsed (s)")
            ax.set_title(f"Convergence: {var_df['scenario_id'].nunique()} scenarios")
            ax.legend(fontsize=8, ncol=2, title="Scenario")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(OUTPUTS_DIR / "exp2_convergence.png", dpi=120)
            plt.close(fig)
            print("-> exp2_convergence.png")

    mean_pess = valid["total_elapsed"].mean() if n_valid else 0

    if var_df is not None and len(var_df) > 0:
        sigma = var_df.groupby("scenario_id")["total_elapsed"].std().dropna().mean()
        sigma_source = (
            f"within-scenario, {var_df['scenario_id'].nunique()} scenarios "
            f"x ~{var_df.groupby('scenario_id').size().mean():.0f} rounds"
        )
    else:
        sigma = ok_data.std() if len(ok_data) > 1 else 1.0
        sigma_source = f"cross-scenario, {len(ok_data)} ok scenarios"

    if sigma < 0.2 and mean_pess > 1.0:
        print(f"WARNING: sigma = {sigma:.3f}s suspiciously small (mean = {mean_pess:.2f}s)")

    n_not_split = (~valid["was_split"]).sum() if n_valid else 0
    total_sub_calls = (
        valid.loc[valid["was_split"], "n_sub_calls"].sum() if n_valid else 0
    )
    avg_calls = (n_not_split + total_sub_calls) / max(n_valid, 1)

    rows = []
    for n in [500, 1000, 5000, 10000, 20000]:
        per_scenario = max(mean_pess, avg_calls * RATE_LIMIT_SECONDS)
        api_calls_needed = n * avg_calls
        rows.append(
            {
                "n_names": n,
                "api_calls_needed": int(round(api_calls_needed)),
                "pure_api_hr": round(n * per_scenario / 3600, 2),
                "quota_periods_needed": round(api_calls_needed / QUOTA_PER_PERIOD, 1),
                "ci95_half_sec": round(1.96 * sigma * np.sqrt(n), 1),
            }
        )
    extrap_df = pd.DataFrame(rows)
    extrap_df.to_csv(OUTPUTS_DIR / "exp2_extrapolation.csv", index=False)
    print(
        f"-> exp2_extrapolation.csv "
        f"(mean={mean_pess:.2f}s, sigma={sigma:.2f}s, source={sigma_source})"
    )

    per_db_times = {}
    for json_str in df["sub_details_json"].dropna():
        for sub in json.loads(json_str):
            if sub["status"] == "ok":
                per_db_times.setdefault(sub["db"], []).append(sub["elapsed"])

    db_means = {}
    db_counts = {}
    if per_db_times:
        for db, times in per_db_times.items():
            db_means[db] = float(np.mean(times))
            db_counts[db] = len(times)
        sorted_dbs = sorted(db_means.keys(), key=lambda d: db_means[d])
        slowest = max(db_means, key=db_means.get)
        fastest = min(db_means, key=db_means.get)

        fig, ax = plt.subplots(figsize=(11, max(5, len(sorted_dbs) * 0.32)))
        bars = ax.barh(
            sorted_dbs,
            [db_means[d] for d in sorted_dbs],
            alpha=0.8,
            color="steelblue",
        )
        for bar, db in zip(bars, sorted_dbs):
            if db == slowest:
                bar.set_color("crimson")
        for i, db in enumerate(sorted_dbs):
            ax.text(
                db_means[db] + 0.05,
                i,
                f"  {db_means[db]:.2f}s  (n={db_counts[db]})",
                va="center",
                fontsize=8,
            )
        ax.set_xlabel("Mean time per single-DB call (seconds)")
        ax.set_ylabel("Database")
        ax.set_title(
            f"{slowest} is {db_means[slowest]/db_means[fastest]:.0f}x slower than {fastest}"
        )
        ax.grid(axis="x", alpha=0.3)
        ax.set_xlim(0, max(db_means.values()) * 1.3)
        fig.tight_layout()
        fig.savefig(OUTPUTS_DIR / "exp2_per_db_latency.png", dpi=120)
        plt.close(fig)
        print("-> exp2_per_db_latency.png")

    if n_valid >= 30:
        formula = "total_elapsed ~ db_count + class_count + name_length + C(name_type)"
        model = smf.ols(formula, data=valid).fit()
        factor_labels = {
            "C(name_type)[T.dictionary]": "Name is a dictionary word (vs coined)",
            "C(name_type)[T.multi_word]": "Name has multiple words (with spaces)",
            "db_count": "Per additional database queried",
            "class_count": "Per additional NICE class",
            "name_length": "Per additional character in name",
        }
        coef_rows = []
        for name in model.params.index:
            if name == "Intercept":
                continue
            coef_rows.append(
                {
                    "factor": factor_labels.get(name, name),
                    "coefficient_s": round(float(model.params[name]), 3),
                    "p_value": round(float(model.pvalues[name]), 4),
                    "significant": "Yes" if model.pvalues[name] < 0.05 else "No",
                }
            )
        ols_df = pd.DataFrame(coef_rows)
        ols_df.to_csv(OUTPUTS_DIR / "exp2_ols_coefficients.csv", index=False)
        print(f"-> exp2_ols_coefficients.csv (R^2 = {model.rsquared:.3f})")

    print("Done.")


if __name__ == "__main__":
    main()
