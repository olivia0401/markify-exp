# Markify Experiments

Two experiments on the Markify trademark API:
1. **Distance Score** — collect score distributions for 70 candidate names so Mark/Jack can calibrate a risk threshold.
2. **Timing** — measure call latency across all 756 Sheet 2 scenarios and extrapolate to 500–20k name batches.

## Setup

```
pip install -r requirements.lock
echo "MARKIFY_API_KEY=<your-key>" > .env
```

## Run

```bash
# Smoke test first (1 API call)
python -m scripts.test_call

# Verify which DB codes work (recommended before big runs)
python -m scripts.probe_db_codes

# Experiment 1 — data collection (resumable, ~hours)
python -m scripts.run_experiment_1
python -m scripts.analyze_experiment_1            # default 10 sample histograms
python -m scripts.analyze_experiment_1 --sample-hists 25  # parametric N

# Experiment 2 — full timing run (756 scenarios, ~hours)
python -m scripts.run_experiment_2
# Variance subset for σ estimation
python -m scripts.run_experiment_2_variance --rounds 30 --per-stratum 2
python -m scripts.analyze_experiment_2
```

All scripts are resumable — if a run is interrupted, re-running skips
scenarios already in the CSV.

## Outputs

`outputs/` (gitignored):

| File | What it is |
|---|---|
| `exp1_results.csv` | Raw per-match data, every (name, db) call |
| `exp1_per_name_summary.csv` | One row per name, max/mean/median/p95 + risk_band |
| `exp1_risk_bands.csv` | Counts per band |
| `exp1_global_histogram.png` | Distribution of all match scores |
| `exp1_max_score_per_name.png` | Per-name max_score distribution (the threshold-relevant view) |
| `exp1_sample_hists/<NAME>.png` | Per-name histograms (parametric N) |
| `exp1_findings.md` | Narrative answer to "is the score useful, what threshold" |
| `exp2_full_results.csv` | One row per Sheet 2 scenario, with split-recovery details |
| `exp2_variance_results.csv` | Variance subset (10 scenarios × N rounds) |
| `exp2_extrapolation.csv` | Estimates for 500/1k/5k/10k/20k names |
| `exp2_factor_analysis.txt` | OLS regression results (univariate + multivariate) |
| `exp2_findings.md` | Narrative summary + production recommendation |
| `exp2_*.png` | Latency distribution / convergence / per-DB latency |

## Methodology notes (read before relying on numbers)

1. **Rate limit**: 2s between calls (Simon's spec). Enforced in
   `src/markify_client.py`.
2. **Cache busting**: `_cb` random URL parameter + `Cache-Control: no-cache`
   headers. Server-side cache behaviour not formally verified — if you
   suspect cache hits, run the same scenario twice and compare elapsed.
3. **Pagination cap (Exp 1)**: `MAX_PAGES_PER_DB = 3` (= 300 results per
   (name, DB)). max_score is reliable; low-score tail is truncated.
4. **EU/US codes fail (Exp 2)**: Sheet 2's `EU` and `US` always return 401.
   Likely fix is `EUIPO` / `USPTO` — verify with `probe_db_codes.py`.
5. **Single-call σ unmeasured**: the variance subset accidentally sampled
   scenarios whose first call always failed (EU/US present), so we measured
   split-recovery σ instead. See `exp2_findings.md` §5.
