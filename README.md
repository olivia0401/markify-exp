# Markify Experiments

Two experiments on the Markify trademark API.

## Setup

```bash
pip install -r requirements.lock
echo "MARKIFY_API_KEY=<your-key>" > .env
```

## Run

### Experiment 1 — Distance Score

```bash
python -m scripts.run_experiment_1
python -m scripts.analyze_experiment_1
python -m scripts.build_exp1_xlsx
```

### Experiment 2 — Timing

```bash
python -m scripts.run_experiment_2
python -m scripts.run_experiment_2_variance --force --rounds 30 --per-stratum 2
python -m scripts.analyze_experiment_2
python -m scripts.build_exp2_xlsx
```

### Smoke test

```bash
python -m scripts.test_call
```

## File structure

```
src/
  config.py             paths, API key, rate limit, timeout
  markify_client.py     Markify API client

scripts/
  test_call.py                  1-call smoke test
  run_experiment_1.py           Exp 1 data collection
  analyze_experiment_1.py       Exp 1 analysis (CSV + PNG)
  build_exp1_xlsx.py            Exp 1 Excel builder
  run_experiment_2.py           Exp 2 main run
  run_experiment_2_variance.py  Exp 2 variance subset
  analyze_experiment_2.py       Exp 2 analysis (CSV + PNG)
  build_exp2_xlsx.py            Exp 2 Excel builder

data/
  Markify tests.xlsx            input names + scenarios
  known_bad_db_codes.json       persisted set of failing DB codes

outputs/    generated CSV + PNG (gitignored)
Results/    final Excel deliverables
```

## CLI flags

- `--max-calls N` (default 1900) — stop after N API calls. Re-run next day to resume.
- `--max-names N` (Exp 1) — process at most N names.
- `--max-scenarios N` (Exp 2) — process at most N scenarios.
- `--rounds N` / `--per-stratum N` (variance) — sample size.
- `--force` (variance) — overwrite existing variance CSV.

## Settings (in `src/markify_client.py` and `src/config.py`)

- `mode = knockout`, `gas = 1`, `min_score = 0`
- 2-second rate limit between calls
- 18-second HTTP timeout
- Cache busting: `_cb` random URL parameter + `Cache-Control: no-cache`
- `EU` / `US` auto-translated to `EUIPO` / `USPTO`
- Quota latch: first quota error stops the run; resume next day

## Resume behaviour

Run scripts skip work already in the CSV. Failed or truncated rows are
auto-redone on the next run with the same command.
