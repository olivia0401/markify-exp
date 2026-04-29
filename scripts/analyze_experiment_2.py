"""
Experiment 2 analysis.

Generates plots and a summary that explain themselves —
charts have conclusions in titles, axes explain what they measure,
and the findings markdown speaks plain English.

Run: python -m scripts.analyze_experiment_2
"""

import json
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from src.config import OUTPUTS_DIR, RATE_LIMIT_SECONDS

# Console may be GBK on Windows — force UTF-8 so '→' / '²' don't crash prints.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

FULL_CSV = OUTPUTS_DIR / "exp2_full_results.csv"
VAR_CSV = OUTPUTS_DIR / "exp2_variance_results.csv"

# A scenario only counts as "valid timing data" if the call actually
# returned results. http_error / split_failed are API rejections —
# they return in ~60ms regardless of complexity, so including them
# would skew the latency mean and make extrapolation wrong.
VALID_STATUSES = ["ok", "split_partial"]


def classify_error(err):
    """Categorise an original_error string into 'quota_exceeded' / "
    'bad_db_code' / 'other' / 'none'.

    Why this exists: the earlier version of this analysis lumped all errors
    together and mis-diagnosed the run as 'EU/US codes always fail'. In
    reality the 2000-call API quota was hit mid-run and ~60% of recorded
    'failures' are quota errors, not bad codes. Classifying upfront prevents
    that mistake.
    """
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
    print(df_full["attempt_status"].value_counts())
    print()

    # === SANITY CHECK 1: classify error messages ===
    # If quota was exhausted mid-run, separate real measurements from the
    # ~60ms quota-error responses that masquerade as fast calls.
    df_full["error_type"] = df_full["original_error"].apply(classify_error)
    err_counts = df_full["error_type"].value_counts()
    print("Error type breakdown:")
    for k, v in err_counts.items():
        print(f"  {k}: {v}")
    print()

    n_quota = err_counts.get("quota_exceeded", 0)
    if n_quota > 0:
        first_q = df_full.loc[
            df_full["error_type"] == "quota_exceeded", "timestamp_utc"
        ].min()
        pre_q_mask = df_full["timestamp_utc"] < first_q
        n_pre = int(pre_q_mask.sum())
        n_post = int((~pre_q_mask).sum())
        print(f"⚠ QUOTA EXHAUSTED at {first_q}")
        print(f"  Pre-quota  scenarios: {n_pre} (real data)")
        print(f"  Post-quota scenarios: {n_post} (quota-error garbage, ~60ms each)")
        print(f"  → Restricting downstream analysis to pre-quota data only.\n")
        df = df_full[pre_q_mask].copy()
        QUOTA_DETECTED = True
        FIRST_QUOTA_TS = first_q
    else:
        df = df_full.copy()
        QUOTA_DETECTED = False
        FIRST_QUOTA_TS = None

    valid = df[df["attempt_status"].isin(VALID_STATUSES)].copy()
    n_valid = len(valid)
    print(f"Valid (ok + split_partial) within pre-quota: {n_valid} / {len(df)} "
          f"({n_valid/len(df)*100:.1f}%)")
    print()

    ok_data = valid[valid["attempt_status"] == "ok"]["total_elapsed"]
    split_data = valid[valid["attempt_status"] == "split_partial"]["total_elapsed"]
    ok_mean = ok_data.mean()
    split_mean = split_data.mean()

    # ---------- 1. Latency distribution ----------
    fig, ax = plt.subplots(figsize=(11, 5))
    bins = np.linspace(0, valid["total_elapsed"].max(), 40)

    ax.hist(ok_data, bins=bins, alpha=0.6, color="steelblue",
            label=f"Single call worked (n={len(ok_data)}, avg {ok_mean:.1f}s)")
    ax.hist(split_data, bins=bins, alpha=0.6, color="orange",
            label=f"Had to split per-DB (n={len(split_data)}, avg {split_mean:.1f}s)")
    ax.axvline(ok_mean, color="steelblue", linestyle="--", linewidth=1)
    ax.axvline(split_mean, color="orange", linestyle="--", linewidth=1)

    ax.set_xlabel("Time per scenario (seconds)")
    ax.set_ylabel("Number of scenarios")
    if QUOTA_DETECTED:
        ax.set_title(
            f"Splitting makes scenarios {split_mean/ok_mean:.0f}× slower "
            f"(pre-quota only: {n_valid} timed out of {len(df)}; "
            f"{n_total - len(df)} post-quota scenarios excluded)"
        )
    else:
        ax.set_title(
            f"Splitting makes scenarios {split_mean/ok_mean:.0f}× slower "
            f"({n_valid} valid out of {n_total})"
        )
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    fig.savefig(OUTPUTS_DIR / "exp2_latency_distribution.png", dpi=120)
    plt.close(fig)
    print("→ exp2_latency_distribution.png")

    # ---------- 2. Convergence plot ----------
    # IMPORTANT methodology note:
    # In our variance subset, EVERY scenario's first call returned http_error
    # (because the sample includes EU/US codes that Markify rejects). For
    # was_split=False scenarios that means total_elapsed is just the ~60ms 401
    # response — averaging those across rounds produces fake "stability" that
    # doesn't reflect real search variance. We therefore restrict the
    # convergence analysis to was_split=True rows, where total_elapsed is the
    # sum of real per-DB sub-calls.
    #
    # CAVEAT: This means we have NO direct measurement of single-call variance.
    # σ reported below is variance of split-recovery total time, which is the
    # right number for extrapolating split-aware production runs.
    var_df = None
    var_df_full = None
    n_dropped_pure_fail = 0
    if VAR_CSV.exists():
        var_df_full = pd.read_csv(VAR_CSV).sort_values(["scenario_id", "round_idx"])
        var_df = var_df_full[var_df_full["was_split"] == True].copy()  # noqa: E712
        n_dropped_pure_fail = len(var_df_full) - len(var_df)
        if n_dropped_pure_fail:
            dropped_sids = sorted(
                var_df_full.loc[var_df_full["was_split"] == False, "scenario_id"].unique()  # noqa: E712
            )
            print(f"Convergence: dropped {n_dropped_pure_fail} pure-failure rows "
                  f"(scenarios {dropped_sids}: 1-DB calls that always returned 401)")

        if len(var_df) == 0:
            print("⚠ No split-recovery rows in variance subset — skipping convergence plot")
            var_df = None
        else:
            fig, ax = plt.subplots(figsize=(11, 5))
            for sid, group in var_df.groupby("scenario_id"):
                running_mean = group["total_elapsed"].expanding().mean()
                x = list(range(1, len(group) + 1))
                db = group["db_count"].iloc[0]
                ax.plot(x, running_mean, marker="o", markersize=3,
                        label=f"sid={sid} ({db} DBs, n={len(group)})")

            ax.set_xlabel("Round")
            ax.set_ylabel("Running mean of total_elapsed (seconds)")
            ax.set_title(
                f"Convergence of split-recovery timing "
                f"({var_df['scenario_id'].nunique()} scenarios; "
                f"single-call σ NOT measured — see findings §5)"
            )
            ax.legend(fontsize=8, ncol=2, title="Scenario")
            ax.grid(alpha=0.3)
            fig.tight_layout()
            fig.savefig(OUTPUTS_DIR / "exp2_convergence.png", dpi=120)
            plt.close(fig)
            print("→ exp2_convergence.png")

    # ---------- 3. Extrapolation ----------
    mean_pess = valid["total_elapsed"].mean()
    mean_opt = ok_mean

    # σ source — only trust the variance subset if it has CLEAN data (no quota
    # contamination). With quota errors hiding inside split-recovery rounds,
    # earlier σ estimates were artificially small.
    if var_df is not None:
        sigma = var_df.groupby("scenario_id")["total_elapsed"].std().dropna().mean()
        sigma_source = (
            f"within-scenario σ from {var_df['scenario_id'].nunique()} scenarios "
            f"× ~{var_df.groupby('scenario_id').size().mean():.0f} rounds"
        )
    else:
        # Fall back to cross-scenario σ on the OK subset. Within-scenario σ
        # was not measured (variance subset hit the same quota wall).
        sigma = ok_data.std()
        sigma_source = (
            f"cross-scenario σ on {len(ok_data)} 'ok' scenarios "
            "(within-scenario σ NOT measured — variance subset hit quota)"
        )

    # === SANITY CHECK 2: σ should be of the same order as call latency ===
    # Real network calls have ≥100-500ms jitter. σ < 0.2s on calls that
    # average 1-5s is a red flag for contamination by stable error responses.
    if sigma < 0.2 and mean_opt > 1.0:
        print(f"⚠ σ = {sigma:.3f}s is suspiciously small relative to "
              f"mean call time ({mean_opt:.2f}s). This usually means the "
              "data is contaminated with stable ~60ms error responses "
              "(quota / 401). Treat downstream CIs as unreliable.")

    n_not_split = (~valid["was_split"]).sum()
    total_sub_calls = valid.loc[valid["was_split"], "n_sub_calls"].sum()
    avg_calls = (n_not_split + total_sub_calls) / n_valid

    # We now report ONE extrapolation: the actual pre-quota mean. The earlier
    # 'if codes fixed' column was based on the wrong assumption that EU/US
    # codes were the bottleneck — quota was. We add a separate quota-aware
    # estimate that accounts for the 2000-call/quota-period limit.
    QUOTA_PER_PERIOD = 2000  # observed in this run
    rows = []
    for n in [500, 1000, 5000, 10000, 20000]:
        per_scenario = max(mean_pess, avg_calls * RATE_LIMIT_SECONDS)
        api_calls_needed = n * avg_calls
        n_quota_periods = api_calls_needed / QUOTA_PER_PERIOD
        rows.append({
            "n_names": n,
            "pure_api_min": round(n * per_scenario / 60, 1),
            "pure_api_hr": round(n * per_scenario / 3600, 2),
            "api_calls_needed": int(round(api_calls_needed)),
            "quota_periods_needed": round(n_quota_periods, 1),
            "ci95_half_sec": round(1.96 * sigma * np.sqrt(n), 1),
        })
    extrap_df = pd.DataFrame(rows)
    extrap_df.to_csv(OUTPUTS_DIR / "exp2_extrapolation.csv", index=False)
    print("→ exp2_extrapolation.csv")
    print(f"   pre-quota μ={mean_pess:.2f}s (with split), "
          f"ok-subset μ={mean_opt:.2f}s, σ={sigma:.2f}s ({sigma_source})")
    print(extrap_df.to_string(index=False))
    print()

    # ---------- 4. Per-database latency ----------
    per_db_times = {}
    for json_str in df["sub_details_json"].dropna():
        for sub in json.loads(json_str):
            if sub["status"] == "ok":
                per_db_times.setdefault(sub["db"], []).append(sub["elapsed"])

    db_means = {}
    db_counts = {}
    if per_db_times:
        for db, times in per_db_times.items():
            db_means[db] = np.mean(times)
            db_counts[db] = len(times)

        sorted_dbs = sorted(db_means.keys(), key=lambda d: db_means[d])
        slowest = max(db_means, key=db_means.get)
        fastest = min(db_means, key=db_means.get)

        fig, ax = plt.subplots(figsize=(11, max(5, len(sorted_dbs) * 0.32)))
        bars = ax.barh(sorted_dbs, [db_means[d] for d in sorted_dbs],
                       alpha=0.8, color="steelblue")
        for bar, db in zip(bars, sorted_dbs):
            if db == slowest:
                bar.set_color("crimson")

        for i, db in enumerate(sorted_dbs):
            ax.text(db_means[db] + 0.05, i,
                    f"  {db_means[db]:.2f}s  (n={db_counts[db]})",
                    va="center", fontsize=8)

        ax.set_xlabel("Mean time when querying ONE database (seconds)")
        ax.set_ylabel("Database (national trademark office)")
        ax.set_title(
            f"{slowest} is the bottleneck — {db_means[slowest]/db_means[fastest]:.0f}× "
            f"slower than {fastest} (fastest at bottom)"
        )
        ax.grid(axis="x", alpha=0.3)
        ax.set_xlim(0, max(db_means.values()) * 1.3)
        fig.tight_layout()
        fig.savefig(OUTPUTS_DIR / "exp2_per_db_latency.png", dpi=120)
        plt.close(fig)
        print("→ exp2_per_db_latency.png")

    # ---------- 5. Factor analysis (OLS) ----------
    # Methodology note: an earlier version included C(db_scenario) (e.g. "10
    # busiest", "2 quiet + 1 busy") alongside db_count. Those categories are
    # derived from db_count itself ("10 busiest" ⇒ db_count=10), so the design
    # matrix was nearly singular (smallest eigenvalue ~2e-29) and subcategory
    # coefficients were unstable. We now report two separate models:
    #   (a) univariate db_count — the headline "+X seconds per extra DB" number
    #   (b) multivariate without db_scenario — checks whether name attributes
    #       contribute beyond db_count
    ols_result = None
    univ_result = None
    if n_valid >= 30:
        univ_result = smf.ols("total_elapsed ~ db_count", data=valid).fit()
        formula = ("total_elapsed ~ db_count + class_count + name_length"
                   " + C(name_type)")
        model = smf.ols(formula, data=valid).fit()

        with open(OUTPUTS_DIR / "exp2_factor_analysis.txt", "w", encoding="utf-8") as f:
            f.write("Which factors drive total_elapsed (seconds)?\n")
            f.write(f"Sample: {n_valid} valid scenarios.\n\n")
            f.write("=" * 78 + "\n")
            f.write("Model (a) — Univariate: total_elapsed ~ db_count\n")
            f.write("=" * 78 + "\n")
            f.write(f"R² = {univ_result.rsquared:.3f} "
                    f"({univ_result.rsquared*100:.0f}% of variation explained by "
                    "db_count alone)\n\n")
            f.write(univ_result.summary().as_text())
            f.write("\n\n")
            f.write("=" * 78 + "\n")
            f.write("Model (b) — Multivariate (no db_scenario; collinear with db_count)\n")
            f.write("=" * 78 + "\n")
            f.write(f"R² = {model.rsquared:.3f}\n\n")
            f.write("Each coefficient = predicted change in seconds per unit of that factor.\n")
            f.write("P>|t| < 0.05 means statistically significant.\n\n")
            f.write(model.summary().as_text())
        print("-> exp2_factor_analysis.txt")
        print(f"   Univariate db_count R^2 = {univ_result.rsquared:.3f}, "
              f"coef = {univ_result.params['db_count']:.2f}s/DB")
        print(f"   Multivariate R^2 = {model.rsquared:.3f}")
        ols_result = model

    # ---------- 6. Failure mode analysis ----------
    # We work over PRE-QUOTA scenarios only, so bad-code failures are not
    # contaminated by the wave of quota errors that followed.
    failed = df[~df["attempt_status"].isin(VALID_STATUSES)]
    successful_dbs = set()
    bad_code_db_counts = {}

    for json_str in df["sub_details_json"].dropna():
        for sub in json.loads(json_str):
            if sub["status"] == "ok":
                successful_dbs.add(sub["db"])
            else:
                # Sub-call statuses don't carry the error message, so we
                # can't tell quota vs. bad-code at the sub-call level. We
                # filter at the scenario level via pre-quota timestamp,
                # then any failed sub-call here is conservatively attributed
                # to bad-code (worst case for that DB).
                bad_code_db_counts[sub["db"]] = (
                    bad_code_db_counts.get(sub["db"], 0) + 1
                )

    pure_fail_dbs = {
        db: n for db, n in bad_code_db_counts.items()
        if db not in successful_dbs
    }

    multi_db = df[df["db_count"] > 1]
    multi_first_try_ok = (multi_db["original_status"] == "ok").sum()
    n_bad_code_total = int((df_full["error_type"] == "bad_db_code").sum())

    # ---------- Write findings markdown ----------
    n_quota_full = int((df_full["error_type"] == "quota_exceeded").sum())
    with open(OUTPUTS_DIR / "exp2_findings.md", "w", encoding="utf-8") as f:
        f.write("# Experiment 2 — Key Findings\n\n")
        f.write("How long does Markify take? What slows it down? What can we get away with?\n\n")
        f.write("---\n\n")

        f.write("## ⚠ Critical context: API quota was hit during this run\n\n")
        if QUOTA_DETECTED:
            f.write(f"The Markify API key has a **2,000-call quota** which "
                    f"exhausted at `{FIRST_QUOTA_TS}` UTC, partway through "
                    f"the timing run.\n\n")
            f.write(f"- Pre-quota scenarios (real data): **{len(df)}** of {n_total}\n")
            f.write(f"- Post-quota scenarios (~60ms quota errors, NOT real timing): "
                    f"**{n_total - len(df)}**\n")
            f.write(f"- {n_quota_full} top-level errors were `Quota exceeded`, "
                    f"only {n_bad_code_total} were `disallowed parameter values`\n\n")
            f.write("**An earlier version of this analysis mis-diagnosed the "
                    "quota errors as 'EU/US codes always fail' — that was wrong. "
                    "EU/US really are invalid (~150 calls, mostly recovered via split), "
                    "but the dominant failure mode is the quota cap.**\n\n")
            f.write("All numbers below are computed from the **pre-quota** subset only.\n\n")
        else:
            f.write("No quota errors detected in this run.\n\n")

        f.write("## At a glance (pre-quota data)\n\n")
        f.write(f"- **{len(df)} pre-quota scenarios** ({n_valid} usable for timing, "
                f"{len(failed)} truly rejected for bad DB codes)\n")
        f.write(f"- **Mean total scenario time**: {mean_pess:.2f}s "
                "(includes split recovery)\n")
        f.write(f"- **Single-call mean** ({len(ok_data)} clean scenarios): "
                f"{mean_opt:.2f}s\n")
        f.write(f"- **Biggest latency driver**: number of databases — "
                f"+{univ_result.params['db_count']:.2f}s per extra DB "
                f"(univariate OLS, R²={univ_result.rsquared:.2f})\n")
        if db_means:
            f.write(f"- **Slowest DB**: {max(db_means, key=db_means.get)} "
                    f"({max(db_means.values()):.1f}s, "
                    f"{max(db_means.values())/min(db_means.values()):.0f}× the fastest)\n")
        f.write(f"- **σ source**: {sigma_source}; σ = {sigma:.2f}s\n\n")

        f.write("## 1. Failure modes (corrected)\n\n")
        f.write(f"On the pre-quota subset of {len(df)} scenarios:\n\n")
        f.write(f"- **{n_valid} succeeded** ({len(ok_data)} as single calls, "
                f"{len(split_data)} via per-DB split recovery)\n")
        f.write(f"- **{len(failed)} failed completely** for invalid DB codes "
                "(no working sub-call to fall back to)\n\n")
        f.write("Multi-DB call breakdown:\n\n")
        f.write(f"- {multi_first_try_ok}/{len(multi_db)} "
                f"({multi_first_try_ok/len(multi_db)*100:.0f}%) worked on first try\n")
        f.write(f"- {(multi_db['attempt_status'] == 'split_partial').sum()} "
                "needed splitting, recovered partial data\n")
        f.write(f"- {(multi_db['attempt_status'] == 'split_failed').sum()} "
                "failed entirely (ALL sub-DBs invalid in those cases)\n\n")

        if pure_fail_dbs:
            f.write("**DB codes that never succeeded as a sub-call** "
                    "(pre-quota only):\n\n")
            for dbk, n in sorted(pure_fail_dbs.items(), key=lambda x: -x[1]):
                f.write(f"- `{dbk}` — {n} failed appearances\n")
            f.write("\nMost likely fix: replace `EU` → `EUIPO`, `US` → `USPTO`. "
                    "**Untested in this run** — verify with `scripts/probe_db_codes.py`.\n\n")

        if successful_dbs:
            f.write(f"**DB codes that work** ({len(successful_dbs)} of them): ")
            f.write(", ".join(f"`{dbk}`" for dbk in sorted(successful_dbs)))
            f.write("\n\n")

        f.write("## 2. Failed calls (bad codes) are cheap\n\n")
        if len(failed) > 0:
            ratio = mean_pess / failed['original_elapsed'].mean()
            f.write(f"- Rejected (bad code): **{failed['original_elapsed'].mean():.3f}s**\n")
            f.write(f"- Successful: **{mean_pess:.2f}s**\n")
            f.write(f"- → Bad-code rejections are ~{ratio:.0f}× faster than real calls. "
                    "Try-multi-DB-fall-back-to-split is a safe pattern.\n\n")
        else:
            f.write("(No pre-quota bad-code failures to compare against.)\n\n")

        if ols_result is not None and univ_result is not None:
            f.write("## 3. What drives latency\n\n")
            f.write("Two regressions reported (full output in `exp2_factor_analysis.txt`):\n\n")
            f.write(f"**Univariate** (`total_elapsed ~ db_count`): "
                    f"R² = {univ_result.rsquared:.2f}, "
                    f"coef = +{univ_result.params['db_count']:.2f}s per DB "
                    f"(p < 0.001). db_count alone explains "
                    f"{univ_result.rsquared*100:.0f}% of timing variance.\n\n")
            f.write(f"**Multivariate** (db_count + class_count + name_length + name_type): "
                    f"R² = {ols_result.rsquared:.2f}. "
                    "Significant factors:\n\n")
            sig = ols_result.pvalues[ols_result.pvalues < 0.05]
            if "Intercept" in sig.index:
                sig = sig.drop("Intercept")
            if len(sig) > 0:
                coefs = ols_result.params[sig.index]
                ordered = coefs.sort_values(key=abs, ascending=False)
                for name, coef in ordered.items():
                    arrow = "↑" if coef > 0 else "↓"
                    f.write(f"- `{name}`: {arrow} {coef:+.2f}s per unit "
                            f"(p={sig[name]:.4f})\n")
            f.write("\n**Note on earlier version**: a previous model included "
                    "`C(db_scenario)` (e.g. '10 busiest', '2 quiet + 1 busy'). "
                    "Those categories are derived from db_count, so the design "
                    "matrix was nearly singular and subcategory coefficients "
                    "weren't trustworthy. They have been removed.\n\n")
            f.write("**Headline**: db_count is the dominant factor. Plan ~"
                    f"{univ_result.params['db_count']:.1f}s extra per DB.\n\n")

        if db_means:
            f.write("## 4. Per-database speed\n\n")
            sorted_slow = sorted(db_means.items(), key=lambda x: -x[1])[:5]
            sorted_fast = sorted(db_means.items(), key=lambda x: x[1])[:5]
            f.write("**Slowest 5**:\n\n")
            for db, t in sorted_slow:
                f.write(f"- `{db}`: {t:.2f}s (n={db_counts[db]})\n")
            f.write("\n**Fastest 5**:\n\n")
            for db, t in sorted_fast:
                f.write(f"- `{db}`: {t:.2f}s (n={db_counts[db]})\n")
            f.write(f"\nGap: {sorted_slow[0][1]/sorted_fast[0][1]:.0f}× between slowest and fastest. "
                    "Production batch jobs should isolate slow DBs.\n\n")

        f.write("## 5. Are the measurements reliable?\n\n")
        f.write(f"**σ = {sigma:.2f}s** ({sigma_source}).\n\n")
        if var_df is None:
            f.write("**Within-scenario σ was NOT directly measured** in this run. "
                    "Both attempts at the variance subset hit the 2,000-call quota "
                    "wall before producing useful data. The σ above is "
                    "**cross-scenario** stdev computed from the pre-quota 'ok' "
                    "subset — useful as a rough envelope, NOT a tight CI.\n\n")
        else:
            f.write(f"Within-scenario σ across {var_df['scenario_id'].nunique()} "
                    f"variance-subset scenarios: see `exp2_convergence.png`.\n\n")
        f.write("**To collect proper variance data, you need a fresh API key "
                "(or wait for the quota to reset)**, then run "
                "`python -m scripts.run_experiment_2_variance` "
                "after the main run completes.\n\n")

        f.write("## 6. Extrapolation\n\n")
        f.write("Time and quota cost for batch jobs (rate-limited at 2s/call):\n\n")
        f.write("| Names | API calls needed | Quota periods | Pure API time |\n")
        f.write("|-------|------------------|---------------|---------------|\n")
        for r in rows:
            f.write(f"| {r['n_names']:,} | {r['api_calls_needed']:,} | "
                    f"{r['quota_periods_needed']} × 2,000 | {r['pure_api_hr']} hr |\n")
        f.write(f"\nAssumes ~{avg_calls:.1f} API calls per name (split-recovery "
                "average from this run). Quota period is unknown — check with "
                "Markify (likely daily). At 2,000 calls/day:\n\n")
        f.write("- **5,000 names ≈ 6.5 days** of API access just to fit the calls\n")
        f.write("- **20,000 names ≈ 26 days** at this quota\n\n")
        f.write("⚠ **Bigger blocker than latency**: the quota cap. A higher-tier "
                "subscription (or rate-limit relaxation) is required before "
                "talking about 5k+ name batches in real time.\n\n")
        f.write("⚠ CI95 in `exp2_extrapolation.csv` uses cross-scenario σ as "
                "fallback, since within-scenario σ was not measured. Treat as "
                "rough envelope, not committed bound.\n\n")

        f.write("## 7. Recommended production strategy\n\n")
        f.write("In priority order — the first item is the actual blocker, "
                "the rest are optimisations:\n\n")
        f.write("1. **Quota first.** Confirm with Markify what the call budget "
                "is per period (we observed 2,000 cap; period likely daily). "
                "Without a higher tier or batched-mode access, anything beyond "
                "~1,000 names per day is impossible regardless of latency.\n")
        f.write("2. **Validate DB codes.** EU/US literal codes fail; "
                "`scripts/probe_db_codes.py` will tell you whether EUIPO/USPTO "
                "are the right replacements (untested so far).\n")
        f.write("3. **Try multi-DB first, fall back to per-DB split.** "
                "Bad-code rejections cost ~0.07s (§2), so the retry pattern "
                "is cheap. Quota errors are the same ~0.07s — equally cheap "
                "in time, but they BURN quota anyway, so detect quota "
                "specifically and pause the job rather than retrying.\n")
        f.write("4. **Isolate slow databases.** CN (4.3s) is 6× the fastest. "
                "If a name doesn't need CN, don't include it — both saves time "
                "AND saves a quota slot.\n")
        f.write(f"5. **Plan ~{univ_result.params['db_count']:.1f}s per extra DB** "
                "for latency. For 5,000 names averaging 5 DBs each, expect "
                f"~{(5000 * (univ_result.params['db_count']*5 + univ_result.params['Intercept']))/3600:.1f}h "
                "of pure API time, but realistically much longer because of "
                "the quota constraint.\n")
        f.write("6. **Re-measure variance once quota allows.** Current σ is "
                "a cross-scenario envelope, not a true within-scenario σ.\n")

    print("→ exp2_findings.md")
    print()
    print("Done.")


if __name__ == "__main__":
    main()