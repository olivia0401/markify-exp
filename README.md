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
