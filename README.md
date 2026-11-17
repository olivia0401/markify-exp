# Markify API Experiments

Two experiments using the Markify trademark API: distance-score analysis and timing measurements. API access is rate-limited; live runs consume your account quota.

## Setup

```bash
pip install -r requirements.lock
```

Set `MARKIFY_API_KEY` in `.env` (see `.env.example` if present). Input data and output paths are configured in `src/config.py`.

## Run

```bash
python -m scripts.run_experiment_1
python -m scripts.analyze_experiment_1
python -m scripts.run_experiment_2
python -m scripts.analyze_experiment_2
```

To build the Excel reports, run `python -m scripts.build_exp1_xlsx` or `python -m scripts.build_exp2_xlsx`. Offline checks: `python -m pytest tests -q`.

## Experiment scope and timing interpretation

This repository contains **API experiments**, not evidence of an integrated or
production-approved trademark-screening service. Experiment 1 examines
matching/distance scores; experiment 2 records response-time and capacity
behaviour for combinations of names, Nice classes and trademark databases.

Experiment 2 uses this fallback when a **multi-database** request times out or
returns an HTTP error:

1. Preserve the original request status and duration.
2. Retry as separate per-database requests, subject to API limits.
3. Record whether all, some, or none of those requests succeed.
4. Persist completed scenarios to CSV and skip them after a restart.

**Timing correction:** In `scripts/run_experiment_2.py`, a split scenario's raw
`total_elapsed` records the duration of its *subcalls*, while
`original_elapsed` records the first failed combined request. The reporting
step in `scripts/analyze_experiment_2.py` adds both to reconstruct the total
API time. Use the corrected analysed totals for capacity estimates; reporting
only the subcalls would underestimate elapsed time. This fixes the **measurement
method**, not the underlying API latency, and does not demonstrate a production
speedup or cost reduction.

## Credentials and public data

Put `MARKIFY_API_KEY` in a local `.env` file (copy `.env.example`); do not
commit real keys. This repository tracks example/test spreadsheets under
`data/` and report workbooks under `Results/`. Before distributing these
files, the repository owner should confirm that all inputs, trademark names
and results are suitable for public release and permitted by their source
terms. This README is not a claim that a confidentiality or data-rights
review has been completed.
